import glob
import os
from torch.utils.tensorboard import SummaryWriter
import datetime
import numpy as np
import torch
import json
import torch.nn as nn

from a2c_ppo_acktr.envs import VecNormalize


# Get a render function
def get_render_func(venv):
    if hasattr(venv, 'envs'):
        return venv.envs[0].render
    elif hasattr(venv, 'venv'):
        return get_render_func(venv.venv)
    elif hasattr(venv, 'env'):
        return get_render_func(venv.env)

    return None


def get_vec_normalize(venv):
    if isinstance(venv, VecNormalize):
        return venv
    elif hasattr(venv, 'venv'):
        return get_vec_normalize(venv.venv)

    return None


# Necessary for my KFAC implementation.
class AddBias(nn.Module):
    def __init__(self, bias):
        super(AddBias, self).__init__()
        self._bias = nn.Parameter(bias.unsqueeze(1))

    def forward(self, x):
        if x.dim() == 2:
            bias = self._bias.t().view(1, -1)
        else:
            bias = self._bias.t().view(1, -1, 1, 1)

        return x + bias


def update_linear_schedule(optimizer, epoch, total_num_epochs, initial_lr):
    """Decreases the learning rate linearly"""
    lr = initial_lr - (initial_lr * (epoch / float(total_num_epochs)))
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr


def init(module, weight_init, bias_init, gain=1):
    weight_init(module.weight.data, gain=gain)
    bias_init(module.bias.data)
    return module


def cleanup_log_dir(log_dir):
    try:
        os.makedirs(log_dir)
    except OSError:
        files = glob.glob(os.path.join(log_dir, '*.monitor.csv'))
        for f in files:
            os.remove(f)


class Logger():
    def __init__(self, args):
        self.args = args
        self.experiment_name = self.get_experiment_name()
        self.tensorboard_writer = None
        self.save_dir = args.save_dir
        self.set_tensorboard()
        self.first_log = False
        save_path = os.path.join(self.save_dir, self.experiment_name)
        try:
            os.makedirs(save_path)
        except OSError:
            pass
        with open('{}/{}/args.txt'.format(self.save_dir, self.experiment_name), 'w+') as f:
            dict_args = vars(args)
            f.writelines(json.dumps(dict_args, indent=4))


    def set_tensorboard(self):
        now = datetime.datetime.now()
        ts_str = now.strftime('%Y-%m-%d_%H-%M-%S')
        self.tensorboard_writer = SummaryWriter(log_dir=os.path.join(self.args.tensorboard_logdir, self.experiment_name, ts_str))

    def get_experiment_name(self):
        args = self.args
        experiment_name = 'env.{}/task.{}-numenvs.{}-obstype.{}-sim.{}/mode.{}-algo.{}-attention.{}-gamma.{}'.format(
            args.env_name,
            args.task_type,
            args.num_processes,
            args.obs_type,
            args.simulator_type,
            args.train_mode,
            args.algo,
            args.attention_type,
            args.gamma)
        return experiment_name

    def log_data(self, j, total_num_steps, start, end, episode_rewards, dist_entropy, value_loss, action_loss, epsilon, successes):
        if self.first_log == True:
            self.first_log = False
            if self.args.tensorboard_logdir is not None:
                self.set_tensorboard()

        print(
            "Updates {}, num timesteps {}, FPS {} "
            "\n Last {} training episodes: mean/median reward {:.1f}/{:.1f}, min/max reward {:.1f}/{:.1f}\n"
                .format(j, total_num_steps,
                        int(total_num_steps / (end - start)),
                        len(episode_rewards), np.mean(episode_rewards),
                        np.median(episode_rewards), np.min(episode_rewards),
                        np.max(episode_rewards), dist_entropy, value_loss,
                        action_loss))

        if self.tensorboard_writer is not None:
            self.tensorboard_writer.add_scalar("sum_reward", np.sum(episode_rewards), total_num_steps)
            # tensorboard_writer.add_scalar("median_reward", np.median(episode_rewards), total_num_steps)
            # tensorboard_writer.add_scalar("min_reward", np.min(episode_rewards), total_num_steps)
            # tensorboard_writer.add_scalar("max_reward", np.max(episode_rewards), total_num_steps)
            self.tensorboard_writer.add_scalar("dist_entropy", dist_entropy, total_num_steps)
            self.tensorboard_writer.add_scalar("losses/value_loss", value_loss, total_num_steps)
            self.tensorboard_writer.add_scalar("losses/action_loss", action_loss, total_num_steps)
            self.tensorboard_writer.add_scalar("info/epsilon", epsilon, total_num_steps)
            self.tensorboard_writer.add_scalar("info/episode", j, total_num_steps)
            self.tensorboard_writer.add_scalar("info/success", successes, total_num_steps)

    def save_model(self, j, actor_critic, envs):
        save_path = os.path.join(self.save_dir, self.experiment_name)

        torch.save([
            actor_critic,
            getattr(get_vec_normalize(envs), 'ob_rms', None)
        ], os.path.join(save_path, "{}.pt".format(j)))
