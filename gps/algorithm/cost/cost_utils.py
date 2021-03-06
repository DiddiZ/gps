"""Utility functions for costs."""
import numpy as np

# Constants for ramp modes
RAMP_CONSTANT = 1
RAMP_LINEAR = 2
RAMP_QUADRATIC = 3
RAMP_FINAL_ONLY = 4


def get_ramp_multiplier(ramp_option, T, wp_final_multiplier=1.0):
    """Returns a time-varying multiplier.

    Args:
        ramp_option: Ramp mode, i.e `RAMP_CONSTANT`, `RAMP_LINEAR`, `RAMP_QUADRATIC`, or `RAMP_FINAL_ONLY`
        T: Time horizon
        wp_final_multiplier: Weight for costs of last step

    Returns:
        A (T,) float vector containing weights for each time step.

    """
    if ramp_option == RAMP_CONSTANT:
        wpm = np.ones(T)
    elif ramp_option == RAMP_LINEAR:
        wpm = (np.arange(T, dtype=np.float32) + 1) / T
    elif ramp_option == RAMP_QUADRATIC:
        wpm = ((np.arange(T, dtype=np.float32) + 1) / T)**2
    elif ramp_option == RAMP_FINAL_ONLY:
        wpm = np.zeros(T)
        wpm[T - 1] = 1.0
    else:
        raise ValueError('Unknown cost ramp requested!')
    wpm[-1] *= wp_final_multiplier
    return wpm


def evall1l2term(wp, d, Jd, Jdd, l1, l2, alpha):
    """Evaluate and compute derivatives for combined l1/l2 norm penalty.

    loss = (0.5 * l2 * d^2) + (l1 * sqrt(alpha + d^2))

    Args:
        wp: T x D matrix with weights for each dimension and time step.
        d: T x D states to evaluate norm on.
        Jd: T x D x Dx Jacobian - derivative of d with respect to state.
        Jdd: T x D x Dx x Dx Jacobian - 2nd derivative of d with respect to state.
        l1: l1 loss weight.
        l2: l2 loss weight.
        alpha: Constant added in square root.

    """
    # Get trajectory length.
    T, _ = d.shape

    # Compute scaled quantities.
    sqrtwp = np.sqrt(wp)
    dsclsq = d * sqrtwp
    dscl = d * wp
    dscls = d * (wp**2)

    # Compute total cost.
    l = 0.5 * np.sum(dsclsq**2, axis=1) * l2 + np.sqrt(alpha + np.sum(dscl**2, axis=1)) * l1

    # First order derivative terms.
    d1 = dscl * l2 + (dscls / np.sqrt(alpha + np.sum(dscl**2, axis=1, keepdims=True)) * l1)
    lx = np.sum(Jd * np.expand_dims(d1, axis=2), axis=1)

    # Second order terms.
    psq = np.expand_dims(np.sqrt(alpha + np.sum(dscl**2, axis=1, keepdims=True)), axis=1)
    d2 = l1 * (
        (np.expand_dims(np.eye(wp.shape[1]), axis=0) * (np.expand_dims(wp**2, axis=1) / psq)) -
        ((np.expand_dims(dscls, axis=1) * np.expand_dims(dscls, axis=2)) / psq**3)
    )
    d2 += l2 * (np.expand_dims(wp, axis=2) * np.tile(np.eye(wp.shape[1]), [T, 1, 1]))

    d1_expand = np.expand_dims(np.expand_dims(d1, axis=-1), axis=-1)
    sec = np.sum(d1_expand * Jdd, axis=1)

    Jd_expand_1 = np.expand_dims(np.expand_dims(Jd, axis=2), axis=4)
    Jd_expand_2 = np.expand_dims(np.expand_dims(Jd, axis=1), axis=3)
    d2_expand = np.expand_dims(np.expand_dims(d2, axis=-1), axis=-1)
    # TODO This multiplication is very slow for higher dimensions
    lxx = np.sum(np.sum(Jd_expand_1 * Jd_expand_2 * d2_expand, axis=1), axis=1)
    lxx += 0.5 * sec + 0.5 * np.transpose(sec, [0, 2, 1])

    return l, lx, lxx


def evallogl2term(wp, d, Jd, Jdd, l1, l2, alpha):
    """Evaluate and compute derivatives for combined l1/l2 norm penalty.

    loss = (0.5 * l2 * d^2) + (0.5 * l1 * log(alpha + d^2))

    Args:
        wp: T x D matrix with weights for each dimension and time step.
        d: T x D states to evaluate norm on.
        Jd: T x D x Dx Jacobian - derivative of d with respect to state.
        Jdd: T x D x Dx x Dx Jacobian - 2nd derivative of d with respect to state.
        l1: l1 loss weight.
        l2: l2 loss weight.
        alpha: Constant added in square root.

    """
    # Get trajectory length.
    T, _ = d.shape

    # Compute scaled quantities.
    sqrtwp = np.sqrt(wp)
    dsclsq = d * sqrtwp
    dscl = d * wp
    dscls = d * (wp**2)

    # Compute total cost.
    l = 0.5 * np.sum(dsclsq**2, axis=1) * l2 + 0.5 * np.log(alpha + np.sum(dscl**2, axis=1)) * l1
    # First order derivative terms.
    d1 = dscl * l2 + (dscls / (alpha + np.sum(dscl**2, axis=1, keepdims=True)) * l1)
    lx = np.sum(Jd * np.expand_dims(d1, axis=2), axis=1)

    # Second order terms.
    psq = np.expand_dims(alpha + np.sum(dscl**2, axis=1, keepdims=True), axis=1)
    # TODO: Need * 2.0 somewhere in following line, or * 0.0 which is wrong but better.
    d2 = l1 * (
        (np.expand_dims(np.eye(wp.shape[1]), axis=0) * (np.expand_dims(wp**2, axis=1) / psq)) -
        ((np.expand_dims(dscls, axis=1) * np.expand_dims(dscls, axis=2)) / psq**2)
    )
    d2 += l2 * (np.expand_dims(wp, axis=2) * np.tile(np.eye(wp.shape[1]), [T, 1, 1]))

    d1_expand = np.expand_dims(np.expand_dims(d1, axis=-1), axis=-1)
    sec = np.sum(d1_expand * Jdd, axis=1)

    Jd_expand_1 = np.expand_dims(np.expand_dims(Jd, axis=2), axis=4)
    Jd_expand_2 = np.expand_dims(np.expand_dims(Jd, axis=1), axis=3)
    d2_expand = np.expand_dims(np.expand_dims(d2, axis=-1), axis=-1)
    lxx = np.sum(np.sum(Jd_expand_1 * Jd_expand_2 * d2_expand, axis=1), axis=1)

    lxx += 0.5 * sec + 0.5 * np.transpose(sec, [0, 2, 1])

    return l, lx, lxx


def evalasymetric(wp, d, Jd, Jdd, alpha):
    """Evaluate and compute derivatives for asymetric penalty.

    loss = 0.5 * d^2 * (sign(d) + alpha)^2

    Args:
        wp: T x D matrix with weights for each dimension and time step.
        d: T x D states to evaluate norm on.
        Jd: T x D x Dx Jacobian - derivative of d with respect to state.
        Jdd: T x D x Dx x Dx Jacobian - 2nd derivative of d with respect to state.
        alpha: Skewness, -1 <= alpha <= 1. Positive values punishes overestimation, lower values underestimation.

    """
    # Get trajectory length.
    T, _ = d.shape

    skew = np.square(wp) * np.square(np.sign(d) + alpha)
    # Compute total cost.
    l = 0.5 * np.sum(np.square(d) * skew, axis=1)

    # First order derivative terms.
    d1 = d * skew
    lx = np.sum(Jd * np.expand_dims(d1, axis=2), axis=1)

    # Second order terms.
    d2 = np.expand_dims(skew, axis=2) * np.tile(np.eye(wp.shape[1]), [T, 1, 1])

    d1_expand = np.expand_dims(np.expand_dims(d1, axis=-1), axis=-1)
    sec = np.sum(d1_expand * Jdd, axis=1)

    Jd_expand_1 = np.expand_dims(np.expand_dims(Jd, axis=2), axis=4)
    Jd_expand_2 = np.expand_dims(np.expand_dims(Jd, axis=1), axis=3)
    d2_expand = np.expand_dims(np.expand_dims(d2, axis=-1), axis=-1)
    # TODO This multiplication is very slow for higher dimensions
    lxx = np.sum(np.sum(Jd_expand_1 * Jd_expand_2 * d2_expand, axis=1), axis=1)

    lxx += 0.5 * sec + 0.5 * np.transpose(sec, [0, 2, 1])

    return l, lx, lxx


def evalexp(wp, d, Jd, Jdd):
    """Evaluate and compute derivatives for exponential penalty.

    loss = e^(wp*d)

    Args:
        wp: T x D matrix with weights for each dimension and time step.
        d: T x D states to evaluate norm on.
        Jd: T x D x Dx Jacobian - derivative of d with respect to state.
        Jdd: T x D x Dx x Dx Jacobian - 2nd derivative of d with respect to state.

    """
    # Get trajectory length.
    T, _ = d.shape

    ex = np.clip(np.exp(d * wp), 0, 1)

    # Compute total cost.
    l = np.sum(ex, axis=1)

    # First order derivative terms.
    d1 = -wp * ex
    lx = np.sum(Jd * np.expand_dims(d1, axis=2), axis=1)

    # Second order terms.
    d2 = np.expand_dims(wp**2 * ex, axis=2) * np.tile(np.eye(wp.shape[1]), [T, 1, 1])

    d1_expand = np.expand_dims(np.expand_dims(d1, axis=-1), axis=-1)
    sec = np.sum(d1_expand * Jdd, axis=1)

    Jd_expand_1 = np.expand_dims(np.expand_dims(Jd, axis=2), axis=4)
    Jd_expand_2 = np.expand_dims(np.expand_dims(Jd, axis=1), axis=3)
    d2_expand = np.expand_dims(np.expand_dims(d2, axis=-1), axis=-1)
    # TODO This multiplication is very slow for higher dimensions
    lxx = np.sum(np.sum(Jd_expand_1 * Jd_expand_2 * d2_expand, axis=1), axis=1)
    lxx += 0.5 * sec + 0.5 * np.transpose(sec, [0, 2, 1])

    return l, lx, lxx
