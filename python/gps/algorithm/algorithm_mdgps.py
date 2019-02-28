""" This file defines the MD-based GPS algorithm. """
import copy
import logging

import numpy as np
import scipy as sp

from gps.algorithm.algorithm import Algorithm, Timer
from gps.algorithm.algorithm_utils import PolicyInfo
from gps.algorithm.config import ALG_MDGPS
from gps.sample.sample_list import SampleList
from gps.visualization import visualize_approximation

LOGGER = logging.getLogger(__name__)


class AlgorithmMDGPS(Algorithm):
    """
    Sample-based joint policy learning and trajectory optimization with
    (approximate) mirror descent guided policy search algorithm.
    """
    def __init__(self, hyperparams):
        config = copy.deepcopy(ALG_MDGPS)
        config.update(hyperparams)
        Algorithm.__init__(self, config)

        policy_prior = self._hyperparams['policy_prior']
        for m in range(self.M):
            self.cur[m].pol_info = PolicyInfo(self._hyperparams)
            self.cur[m].pol_info.policy_prior = \
                    policy_prior['type'](policy_prior)

        self.policy_opt = self._hyperparams['policy_opt']['type'](
            self._hyperparams['policy_opt'], self.dO, self.dU
        )

        self.traj_opt = hyperparams['traj_opt']['type'](
            hyperparams['traj_opt']
        )

    def iteration(self, sample_lists, _):
        """
        Run iteration of MDGPS-based guided policy search.

        Args:
            sample_lists: List of SampleList objects for each condition.
            _: to match parent class
        """
        # Store the samples and evaluate the costs.
        for m in range(self.M):
            self.cur[m].sample_list = sample_lists[m]
            self._eval_cost(m)

        # Update dynamics linearizations.
        self._update_dynamics()

        # On the first iteration, need to catch policy up to init_traj_distr.
        if self.iteration_count == 0:
            self.new_traj_distr = [
                self.cur[cond].traj_distr for cond in range(self.M)
            ]
            self._update_policy(initial_policy=True)

        # Update policy linearizations.
        with Timer(self.timers, 'pol_lin'):
            for m in range(self.M):
                self._update_policy_fit(m)

        # C-step
        if self.iteration_count > 0:
            self._stepadjust()
        self._update_trajectories()

        # S-step
        with Timer(self.timers, 'pol_update'):
            self._update_policy()

        # Prepare for next iteration
        self._advance_iteration_variables()

    def _update_policy(self, initial_policy=False):
        """ Compute the new policy. """
        dU, dO, T = self.dU, self.dO, self.T
        N = len(self.cur[0].sample_list)

        X = np.empty((self.M, N, T, dO))
        mu = np.empty((self.M, N, T, dU))
        K = np.empty((self.M, T, dU, dO))
        k = np.empty((self.M, T, dU))
        prc = np.empty((self.M, T, dU, dU))

        # Iterate over conditions m
        for m in range(self.M):
            samples = self.cur[m].sample_list
            traj = self.new_traj_distr[m]

            # Shape traj.K: 20,4,13
            # Shape traj.k: 20,4
            K[m] = traj.K
            k[m] = traj.k
            prc[m] = traj.inv_pol_covar

            # Iterate over samples n
            # Get time-indexed actions.
            for n in range(N):
                X[m, n] = samples[n].get_X()

                # Iterate over time t
                for t in range(self.T):
                    mu[m, n, t] = K[m, t].dot(X[m, n, t]) + k[m, t]

        # Shape K:      4,20,4,13           cond, time, action, state
        # Shape prc:    4,20,4,4            cond, time, action, action
        # Shape X:      4,5,20,13           cond, sample, time, state
        # Shape mu:     4,5,20,4            cond, sample, time, action
        self.policy_opt.update(X=X, mu=mu, prc=prc, K=K, k=k, initial_policy=initial_policy)

        # Visualize actions
        u_approx = self.policy_opt.prob(X[0, :1, :, :])[0][0]
        visualize_approximation(
            self._data_files_dir + 'plot_gps_action-m%02d-%02d-%02d' % (0, 0, self.iteration_count),
            mu[0, 0],
            u_approx,
            y_label='$\\mathbf{u}$',
            dim_label_pattern='$\\mathbf{u}_t[%d]$',
        )

    def _update_policy_fit(self, m):
        """
        Re-estimate the local policy values in the neighborhood of the
        trajectory.
        Args:
            m: Condition
            init: Whether this is the initial fitting of the policy.
        """
        dX, dU, T = self.dX, self.dU, self.T
        # Choose samples to use.
        samples = self.cur[m].sample_list
        N = len(samples)
        pol_info = self.cur[m].pol_info
        X = samples.get_X()
        obs = samples.get_obs().copy()
        pol_mu, pol_sig = self.policy_opt.prob(obs)[:2]
        pol_info.pol_mu, pol_info.pol_sig = pol_mu, pol_sig

        # Update policy prior.
        policy_prior = pol_info.policy_prior
        samples = SampleList(self.cur[m].sample_list)
        mode = self._hyperparams['policy_sample_mode']
        policy_prior.update(samples, self.policy_opt, mode)

        # Fit linearization and store in pol_info.
        pol_info.pol_K, pol_info.pol_k, pol_info.pol_S = \
                policy_prior.fit(X, pol_mu, pol_sig)
        for t in range(T):
            pol_info.chol_pol_S[t, :, :] = \
                    sp.linalg.cholesky(pol_info.pol_S[t, :, :])

        # Visualize pol lin
        if m==0:
            self.visualize_policy_linearization(m, 'pol_lin')

    def _advance_iteration_variables(self):
        """
        Move all 'cur' variables to 'prev', reinitialize 'cur'
        variables, and advance iteration counter.
        """
        Algorithm._advance_iteration_variables(self)
        for m in range(self.M):
            self.cur[m].traj_info.last_kl_step = \
                    self.prev[m].traj_info.last_kl_step
            self.cur[m].pol_info = copy.deepcopy(self.prev[m].pol_info)

    def _stepadjust(self):
        """
        Calculate new step sizes. This version uses the same step size
        for all conditions.
        """
        # Compute previous cost and previous expected cost.
        prev_M = len(self.prev) # May be different in future.
        prev_laplace = np.empty(prev_M)
        prev_mc = np.empty(prev_M)
        prev_predicted = np.empty(prev_M)
        for m in range(prev_M):
            prev_nn = self.prev[m].pol_info.traj_distr()
            prev_lg = self.prev[m].new_traj_distr

            # Compute values under Laplace approximation. This is the policy
            # that the previous samples were actually drawn from under the
            # dynamics that were estimated from the previous samples.
            prev_laplace[m] = self.traj_opt.estimate_cost(
                    prev_nn, self.prev[m].traj_info
            ).sum()
            # This is the actual cost that we experienced.
            prev_mc[m] = self.prev[m].cs.mean(axis=0).sum()
            # This is the policy that we just used under the dynamics that
            # were estimated from the prev samples (so this is the cost
            # we thought we would have).
            prev_predicted[m] = self.traj_opt.estimate_cost(
                    prev_lg, self.prev[m].traj_info
            ).sum()

        # Compute current cost.
        cur_laplace = np.empty(self.M)
        cur_mc = np.empty(self.M)
        for m in range(self.M):
            cur_nn = self.cur[m].pol_info.traj_distr()
            # This is the actual cost we have under the current trajectory
            # based on the latest samples.
            cur_laplace[m] = self.traj_opt.estimate_cost(
                    cur_nn, self.cur[m].traj_info
            ).sum()
            cur_mc[m] = self.cur[m].cs.mean(axis=0).sum()

        # Compute predicted and actual improvement.
        prev_laplace = prev_laplace.mean()
        prev_mc = prev_mc.mean()
        prev_predicted = prev_predicted.mean()
        cur_laplace = cur_laplace.mean()
        cur_mc = cur_mc.mean()
        if self._hyperparams['step_rule'] == 'laplace':
            predicted_impr = prev_laplace - prev_predicted
            actual_impr = prev_laplace - cur_laplace
        elif self._hyperparams['step_rule'] == 'mc':
            predicted_impr = prev_mc - prev_predicted
            actual_impr = prev_mc - cur_mc
        LOGGER.debug('Previous cost: Laplace: %f, MC: %f',
                     prev_laplace, prev_mc)
        LOGGER.debug('Predicted cost: Laplace: %f', prev_predicted)
        LOGGER.debug('Actual cost: Laplace: %f, MC: %f',
                     cur_laplace, cur_mc)

        for m in range(self.M):
            self._set_new_mult(predicted_impr, actual_impr, m)

    def compute_costs(self, m, eta):
        """ Compute cost estimates used in the LQR backward pass. """
        traj_info, traj_distr = self.cur[m].traj_info, self.cur[m].traj_distr
        pol_info = self.cur[m].pol_info
        T, dU, dX = traj_distr.T, traj_distr.dU, traj_distr.dX
        Cm, cv = np.copy(traj_info.Cm), np.copy(traj_info.cv)

        PKLm = np.zeros((T, dX + dU, dX + dU))
        PKLv = np.zeros((T, dX + dU))
        fCm, fcv = np.zeros(Cm.shape), np.zeros(cv.shape)
        for t in range(T):
            # Policy KL-divergence terms.
            inv_pol_S = np.linalg.solve(
                pol_info.chol_pol_S[t, :, :], np.linalg.solve(pol_info.chol_pol_S[t, :, :].T, np.eye(dU))
            )
            KB, kB = pol_info.pol_K[t, :, :], pol_info.pol_k[t, :]
            PKLm[t, :, :] = np.vstack(
                [
                    np.hstack([KB.T.dot(inv_pol_S).dot(KB), -KB.T.dot(inv_pol_S)]),
                    np.hstack([-inv_pol_S.dot(KB), inv_pol_S])
                ]
            )
            PKLv[t, :] = np.concatenate([KB.T.dot(inv_pol_S).dot(kB), -inv_pol_S.dot(kB)])
            fCm[t, :, :] = (
                Cm[t, :, :] + self._hyperparams['K_regularization'] * np.linalg.norm(traj_distr.K[t], np.inf) *
                np.eye(dX + dU) + PKLm[t, :, :] * eta
            ) / (eta)
            fcv[t, :] = (cv[t, :] + PKLv[t, :] * eta) / (eta)

        return fCm, fcv
