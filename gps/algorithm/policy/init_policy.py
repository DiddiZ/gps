"""This module provides methods to generate initial policies."""

import numpy as np

from gps.algorithm.policy.lin_gauss_policy import LinearGaussianPolicy


def init_pol(hyperparams):
    """Generates an initial policy of constant actions with added noise."""
    dU, dX = hyperparams['dU'], hyperparams['dX']
    T = hyperparams['T']

    K = np.zeros((T, dU, dX))
    k = np.empty((T, dU))
    PSig = np.empty((T, dU, dU))
    cholPSig = np.empty((T, dU, dU))
    inv_pol_covar = np.empty((T, dU, dU))

    for t in range(T):
        k[t] = hyperparams['init_const']
        PSig[t] = hyperparams['init_var']
        cholPSig[t] = np.linalg.cholesky(PSig[t])
        inv_pol_covar[t] = np.linalg.inv(PSig[t])

    return LinearGaussianPolicy(K, k, PSig, cholPSig, inv_pol_covar)


def init_pol_ctr(hyperparams):
    """Generates an initial policy by loading linear controllers from a file."""
    dU, dX = hyperparams['dU'], hyperparams['dX']
    T = hyperparams['T']
    data = np.load(hyperparams['ctr_file'])

    K = np.empty((T, dU, dX))
    k = np.empty((T, dU))
    prc = np.empty((T, dU, dU))

    K[:-1] = data['K'][0]
    K[-1] = np.zeros((dU, dX))
    k[:-1] = data['k'][0]
    k[-1] = np.zeros((dU))
    prc[:-1] = data['prc'][0]
    prc[-1] = np.eye(dU)

    PSig = np.empty((T, dU, dU))
    cholPSig = np.empty((T, dU, dU))
    inv_pol_covar = np.empty((T, dU, dU))

    for t in range(T):
        PSig[t] = np.linalg.inv(prc[t])
        cholPSig[t] = np.linalg.cholesky(PSig[t])
        inv_pol_covar[t] = np.linalg.inv(PSig[t])

    return LinearGaussianPolicy(K, k, PSig, cholPSig, inv_pol_covar)
