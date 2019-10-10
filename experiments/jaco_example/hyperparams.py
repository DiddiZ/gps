""" Hyperparameters for JACO trajectory optimization experiment. """


import time
from datetime import datetime
import numpy as np
from sklearn.preprocessing import StandardScaler

from __main__ import __file__ as main_filepath
from gps import __file__ as gps_filepath
from gps.agent.ros_jaco.agent_ros_jaco import AgentROSJACO
from gps.algorithm.algorithm_traj_opt import AlgorithmTrajOpt
from gps.algorithm.traj_opt.traj_opt_lqr_python import TrajOptLQRPython
from gps.algorithm.cost.cost_fk import CostFK
from gps.algorithm.cost.cost_action import CostAction
from gps.algorithm.cost.cost_sum import CostSum
from gps.algorithm.cost.cost_state import CostState
from gps.algorithm.cost.cost_utils import evallogl2term
from gps.algorithm.dynamics.dynamics_lr_prior import DynamicsLRPrior
from gps.algorithm.dynamics.dynamics_prior_gmm import DynamicsPriorGMM
from gps.agent.opcua_azo.init_policy import init_azo_pol
from gps.agent.ros_jaco.util import load_pose_from_npz
from gps.proto.gps_pb2 import JOINT_ANGLES, JOINT_VELOCITIES, END_EFFECTOR_POINTS, ACTION, TRIAL_ARM, JOINT_SPACE

EE_POINTS = np.array([[0.04, -0.03, -0.18], [-0.04, -0.03, -0.18], [0.0, 0.03, -0.12]])
#EE_POINTS = np.array([[0.04, -0.10, -0.19], [-0.04, -0.10, -0.19], [0.0, -0.08, -0.12]])
#EE_POINTS = np.array([[0.00, -0.0, -0.0], [-0.00, -0.00, -0.19], [0.0, -0.02, 0.0]])
#EE_POINTS = np.array([[0.00, -0.0, -0.0], [-0.00, -0.00, -0.0], [0.0, -0.0, 0.0]])

SENSOR_DIMS = {
    JOINT_ANGLES: 6,
    JOINT_VELOCITIES: 6,
    END_EFFECTOR_POINTS: 3 * EE_POINTS.shape[0],
    ACTION: 6,
}

PR2_GAINS = np.array([7.09, 2.3, 1.5, 1.2, 1.8, 0.1])

BASE_DIR = '/'.join(str.split(gps_filepath, '/')[:-2])
EXP_DIR = BASE_DIR + '/../experiments/jaco_example/'

common = {
    'experiment_name': 'my_experiment' + '_' + datetime.strftime(datetime.now(), '%m-%d-%y_%H-%M'),
    'experiment_dir': EXP_DIR,
    'data_files_dir': EXP_DIR + 'data_files/',
    'cost_log_dir': EXP_DIR + 'cost_log/',
    'target_filename': EXP_DIR + 'target.npz',
    'log_filename': EXP_DIR + 'log.txt',
    #'train_conditions': [0, 1],
    #'test_conditions': [1,2],
    'conditions': 1,
    'experiment_ID': '1' + time.ctime(),
}

# Set up each condition.
x0s = []
x_tgts = []
ee_tgts = []
reset_conditions = []
for i in range(common['conditions']):

    ja_x0_, ee_pos_x0, ee_rot_x0 = load_pose_from_npz(common['target_filename'], 'trial_arm', str(i), 'initial')
    ja_tgt, ee_pos_tgt, ee_rot_tgt = load_pose_from_npz(common['target_filename'], 'trial_arm', str(i), 'target')
    ee_tgt = np.ndarray.flatten(
        # get_ee_points(EE_POINTS, ee_pos_tgt, ee_rot_tgt).T
        ee_pos_tgt
    )

    x_tgt = np.zeros(21)
    jv_tgt = np.zeros(6)
    x_tgt[:6] = ja_tgt
    x_tgt[6:12] = jv_tgt
    #x_tgt[12:15] = ee_tgt
    x_tgt[12:21] = ee_tgt

    ja_x0 = ja_x0_[:6]
    x0 = np.zeros(21)
    x0[:6] = ja_x0
    #x0[12:(12+3*EE_POINTS.shape[0])] = np.ndarray.flatten(
    #    get_ee_points(EE_POINTS, ee_pos_x0, ee_rot_x0).T
    #)

    reset_condition = {
        TRIAL_ARM: {
            'mode': JOINT_SPACE,
            'data': x0[0:6],
        },
    }

    x_tgts.append(x_tgt)
    x0s.append(x0)
    ee_tgts.append(ee_tgt)
    reset_conditions.append(reset_condition)

scaler = StandardScaler()
scaler.mean_ = np.array(
    [
        5.69780562e+00, 3.80896384e+00, 1.24253418e+00, 3.72482914e+00, 1.49177275e+00, 2.02358381e+00, -4.64449186e-03,
        -2.23377971e-03, -4.64136383e-03, -2.34022680e-02, 9.61034804e-04, -4.70504173e-03, -2.66579842e-01,
        -6.55938247e-01, 4.02926553e-01, -3.40294096e-01, -6.60283312e-01, 3.88512372e-01, -2.93010349e-01,
        -5.93442117e-01, 3.65402165e-01
    ]
)
scaler.scale_ = np.array(
    [
        0.07355964, 0.0573365, 0.0659405, 0.17928623, 0.11548199, 0.10533905, 0.01886712, 0.02391924, 0.02651126,
        0.0593647, 0.05646052, 0.05077679, 0.05350441, 0.06377112, 0.04023324, 0.05297568, 0.05246556, 0.04690082,
        0.04350054, 0.05892672, 0.05874824
    ]
)

agent = {
    'type': AgentROSJACO,
    'dt': 0.05,
    'conditions': common['conditions'],
    'T': 80,
    'x0': x0s,
    'ee_points': EE_POINTS,
    'dee_tgt': 3 * EE_POINTS.shape[0],
    'exp_x_tgts': x_tgts,
    'dtgtX': 6,
    'include_tgt': False,
    'ee_points_tgt': ee_tgts,
    'reset_conditions': reset_conditions,
    'sensor_dims': SENSOR_DIMS,
    'state_include': [JOINT_ANGLES, JOINT_VELOCITIES, END_EFFECTOR_POINTS],
    'actions_include': [ACTION],
    'end_effector_points': EE_POINTS,
    'obs_include': [JOINT_ANGLES, JOINT_VELOCITIES, END_EFFECTOR_POINTS],
    'random_reset': False,
    'scaler': scaler,
    'smooth_noise': True,
    'smooth_noise_var': 2.0,
    'smooth_noise_renormalize': True,
}

algorithm = {
    'type': AlgorithmTrajOpt,
    #'train_conditions': common['train_conditions'],
    #'test_conditions': common['test_conditions'],
    'conditions': common['conditions'],
    'exp_x_tgts': x_tgts,
    'include_tgt': agent['include_tgt'],
    'dtgtX': agent['dtgtX'],
    'dee_tgt': agent['dee_tgt'],
    'ee_points_tgt': agent['ee_points_tgt'],
    'iterations': 15,
    'kl_step': 1.0,
    'min_step_mult': 0.5,
    'max_step_mult': 10.0,
}

algorithm['init_traj_distr'] = {
    'type': init_azo_pol,
    'init_const': np.array([-1.25, 0.05, -0.65, -0.01, 0, 0]),
    'init_var': 0.5 * np.eye(SENSOR_DIMS[ACTION]),
    'dt': agent['dt'],
    'T': agent['T'],
}

algorithm['traj_opt'] = {
    'type': TrajOptLQRPython,
}

torque_cost = {
    'type': CostAction,
    'wu': 1e-2 / PR2_GAINS,
    'name': 'Action',
    'target_state': np.zeros(SENSOR_DIMS[ACTION]),
}

state_cost_ja = {
    'type': CostState,
    'data_types':
        {
            JOINT_ANGLES:
                {
                    'wp': np.ones(6),  # Target size
                    'target_state': (x_tgts[0][:6] - scaler.mean_[:6]) / scaler.scale_[:6],
                },
        },
    'name': 'ja dist',
}

state_cost = {
    'type': CostFK,
    'wp': np.tile([1, 0.1, 1], 3),
    'target_end_effector': (np.zeros(9) - scaler.mean_[-9:]) / scaler.scale_[-9:],
    'l1': 0.1,
    'l2': 10.0,
    'alpha': 1e-6,
    'evalnorm': evallogl2term,
    'name': 'EE dist',
}

algorithm['cost'] = {
    'type': CostSum,
    'costs': [torque_cost, state_cost],
    'weights': [1.0, 1.0],
}

algorithm['dynamics'] = {
    'type': DynamicsLRPrior,
    'regularization': 1e-6,
    'prior':
        {
            'type': DynamicsPriorGMM,
            'max_clusters': 8,
            'min_samples_per_cluster': 40,
            'max_samples': 40,
            'strength': 1,
        },
}


def progress_metric(X):
    return np.mean(np.linalg.norm((scaler.inverse_transform(X[-1:])[0, -9:] - ee_tgts[0]).reshape(3, 3), axis=1))


config = {
    'iterations': algorithm['iterations'],
    'num_samples': 5,
    'num_lqr_samples_static': 1,
    'num_lqr_samples_random': 0,
    'num_pol_samples_static': 0,
    'num_pol_samples_random': 0,
    'verbose_trials': 0,
    'common': common,
    'min_iteration_for_testing': 15,
    'agent': agent,
    'algorithm': algorithm,
    'experiment_ID': common['experiment_ID'],
    'dir': common['cost_log_dir'],
    'random_seed': 72,
    'traing_progress_metric': progress_metric,
}

common['info'] = (
    'exp_name: ' + str(common['experiment_name']) + '\n'
    'alg_type: ' + str(algorithm['type'].__name__) + '\n'
    'alg_dyn:  ' + str(algorithm['dynamics']['type'].__name__) + '\n'
    'alg_cost: ' + str(algorithm['cost']['type'].__name__) + '\n'
    'iterations: ' + str(config['iterations']) + '\n'
    'conditions: ' + str(algorithm['conditions']) + '\n'
    'samples:    ' + str(config['num_samples']) + '\n'
)

param_str = 'jaco_lqr'
param_str += '-random' if agent['random_reset'] else '-static'
param_str += '-M%d' % config['common']['conditions']
param_str += '-%ds' % config['num_samples']
param_str += '-T%d' % agent['T']
common['data_files_dir'] += '%s_%d/' % (param_str, config['random_seed'])

# Only make changes to filesystem if loaded by training process
if main_filepath[-11:].replace('\\', '/') == 'gps/main.py':
    from pathlib import Path
    from shutil import copy2

    # Make expirement folder and copy hyperparams
    Path(common['data_files_dir']).mkdir(parents=True, exist_ok=False)
    copy2(EXP_DIR + 'hyperparams.py', common['data_files_dir'])
