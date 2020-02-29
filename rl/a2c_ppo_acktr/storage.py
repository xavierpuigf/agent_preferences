import torch
from torch.utils.data.sampler import BatchSampler, SubsetRandomSampler


def _flatten_helper(T, N, _tensor):
    return _tensor.view(T * N, *_tensor.size()[2:])


class RolloutStorage(object):
    def __init__(self, num_steps, num_processes, obs_shapes, action_space,
                 recurrent_hidden_state_size):
        self.obs = {k: torch.zeros(num_steps + 1, num_processes, *obs_shape) for k, obs_shape in obs_shapes.items()}

        self.recurrent_hidden_states = torch.zeros(
            num_steps + 1, num_processes, recurrent_hidden_state_size)
        self.rewards = torch.zeros(num_steps, num_processes, 1)
        self.value_preds = torch.zeros(num_steps + 1, num_processes, 1)
        self.returns = torch.zeros(num_steps + 1, num_processes, 1)
        self.action_log_probs = [torch.zeros(num_steps, num_processes, 1) for _ in range(len(action_space))]


        action_shape = []
        for act_space in action_space:
            if act_space.__class__.__name__ == 'Discrete':
                action_shape.append(1)
            else:
                action_shape.append(act_space.shape[0])
        try:
            self.actions = [torch.zeros(num_steps, num_processes, act_shape) for act_shape in action_shape]
        except:
            import pdb
            pdb.set_trace()

        for i, act_space in enumerate(action_space): 
            if act_space.__class__.__name__ == 'Discrete':
                self.actions[i] = self.actions[i].long()

        self.masks = torch.ones(num_steps + 1, num_processes, 1)

        # Masks that indicate whether it's a true terminal state
        # or time limit end state
        self.bad_masks = torch.ones(num_steps + 1, num_processes, 1)

        self.num_steps = num_steps
        self.step = 0

    def to(self, device):
        self.obs = {kob: ob.to(device) for kob, ob in self.obs.items()}
        self.recurrent_hidden_states = self.recurrent_hidden_states.to(device)
        self.rewards = self.rewards.to(device)
        self.value_preds = self.value_preds.to(device)
        self.returns = self.returns.to(device)
        self.action_log_probs = [action_log_probs.to(device) for action_log_probs in self.action_log_probs]
        self.actions = [action.to(device) for action in self.actions]
        self.masks = self.masks.to(device)
        self.bad_masks = self.bad_masks.to(device)

    def insert(self, obs, recurrent_hidden_states, actions, action_log_probs,
               value_preds, rewards, masks, bad_masks):
        for type_ob_id in self.obs.keys():
            self.obs[type_ob_id][self.step + 1].copy_(obs[type_ob_id])
        self.recurrent_hidden_states[self.step +
                                     1].copy_(recurrent_hidden_states)
        
        for type_action_id in range(len(self.actions)):
            self.actions[type_action_id][self.step].copy_(actions[type_action_id])
            self.action_log_probs[type_action_id][self.step].copy_(action_log_probs[type_action_id])
        self.value_preds[self.step].copy_(value_preds)
        self.rewards[self.step].copy_(rewards)
        self.masks[self.step + 1].copy_(masks)
        self.bad_masks[self.step + 1].copy_(bad_masks)

        self.step = (self.step + 1) % self.num_steps

    def after_update(self):
        for type_ob_id in self.obs.keys():
            self.obs[type_ob_id][0].copy_(self.obs[type_ob_id][-1])
        self.recurrent_hidden_states[0].copy_(self.recurrent_hidden_states[-1])
        self.masks[0].copy_(self.masks[-1])
        self.bad_masks[0].copy_(self.bad_masks[-1])

    def compute_returns(self,
                        next_value,
                        use_gae,
                        gamma,
                        gae_lambda,
                        use_proper_time_limits=True):
        if use_proper_time_limits:
            if use_gae:
                self.value_preds[-1] = next_value
                gae = 0
                for step in reversed(range(self.rewards.size(0))):
                    delta = self.rewards[step] + gamma * self.value_preds[
                        step + 1] * self.masks[step +
                                               1] - self.value_preds[step]
                    gae = delta + gamma * gae_lambda * self.masks[step +
                                                                  1] * gae
                    gae = gae * self.bad_masks[step + 1]
                    self.returns[step] = gae + self.value_preds[step]
            else:
                self.returns[-1] = 0 #next_value
                for step in reversed(range(self.rewards.size(0))):
                    self.returns[step] = (self.returns[step + 1] * \
                        gamma * self.masks[step + 1] + self.rewards[step]) * self.bad_masks[step + 1] \
                        + (1 - self.bad_masks[step + 1]) * self.value_preds[step]
        else:
            if use_gae:
                self.value_preds[-1] = next_value
                gae = 0
                for step in reversed(range(self.rewards.size(0))):
                    delta = self.rewards[step] + gamma * self.value_preds[
                        step + 1] * self.masks[step +
                                               1] - self.value_preds[step]
                    gae = delta + gamma * gae_lambda * self.masks[step +
                                                                  1] * gae
                    self.returns[step] = gae + self.value_preds[step]
            else:
                self.returns[-1] = 0 #next_value
                for step in reversed(range(self.rewards.size(0))):
                    self.returns[step] = self.returns[step + 1] * \
                        gamma * self.masks[step + 1] + self.rewards[step]

    def feed_forward_generator(self,
                               advantages,
                               num_mini_batch=None,
                               mini_batch_size=None):
        num_steps, num_processes = self.rewards.size()[0:2]
        batch_size = num_processes * num_steps

        if mini_batch_size is None:
            assert batch_size >= num_mini_batch, (
                "PPO requires the number of processes ({}) "
                "* number of steps ({}) = {} "
                "to be greater than or equal to the number of PPO mini batches ({})."
                "".format(num_processes, num_steps, num_processes * num_steps,
                          num_mini_batch))
            mini_batch_size = batch_size // num_mini_batch
        sampler = BatchSampler(
            SubsetRandomSampler(range(batch_size)),
            mini_batch_size,
            drop_last=True)
        for indices in sampler:
            obs_batch = [ob[:-1].view(-1, *ob.size()[2:])[indices] for ob in self.obs]
            recurrent_hidden_states_batch = self.recurrent_hidden_states[:-1].view(
                -1, self.recurrent_hidden_states.size(-1))[indices]
            actions_batch = [actions_type.view(-1,
                                              actions_type.size(-1))[indices] for actions_type in self.actions]
            value_preds_batch = self.value_preds[:-1].view(-1, 1)[indices]
            return_batch = self.returns[:-1].view(-1, 1)[indices]
            masks_batch = self.masks[:-1].view(-1, 1)[indices]
            old_action_log_probs_batch = [action_log_probs.view(-1, 
                                                                1)[indices] for action_log_probs in self.action_log_probs]
            if advantages is None:
                adv_targ = None
            else:
                adv_targ = advantages.view(-1, 1)[indices]

            yield obs_batch, recurrent_hidden_states_batch, actions_batch, \
                value_preds_batch, return_batch, masks_batch, old_action_log_probs_batch, adv_targ

    def recurrent_generator(self, advantages, num_mini_batch):
        num_processes = self.rewards.size(1)
        assert num_processes >= num_mini_batch, (
            "PPO requires the number of processes ({}) "
            "to be greater than or equal to the number of "
            "PPO mini batches ({}).".format(num_processes, num_mini_batch))
        num_envs_per_batch = num_processes // num_mini_batch
        perm = torch.randperm(num_processes)
        for start_ind in range(0, num_processes, num_envs_per_batch):
            obs_batch = [[] for _ in self.obs]
            recurrent_hidden_states_batch = []
            actions_batch = [[] for _ in self.actions]
            value_preds_batch = []
            return_batch = []
            masks_batch = []
            old_action_log_probs_batch = [[] for _ in self.actions]
            adv_targ = []

            for offset in range(num_envs_per_batch):
                ind = perm[start_ind + offset]
                for obs_type_id in range(len(self.obs)):
                    obs_batch[obs_type_id].append(self.obs[obs_type_id][:-1, ind])
                recurrent_hidden_states_batch.append(
                    self.recurrent_hidden_states[0:1, ind])

                for action_type_id in range(len(self.obs)):
                    actions_batch[action_type_id].append(self.actions[action_type_id][:, ind])
                    old_action_log_probs_batch[action_type_id].append(
                            self.action_log_probs[action_type_id][:, ind])
                    value_preds_batch.append(self.value_preds[:-1, ind])
                return_batch.append(self.returns[:-1, ind])
                masks_batch.append(self.masks[:-1, ind])
                adv_targ.append(advantages[:, ind])

            T, N = self.num_steps, num_envs_per_batch
            # These are all tensors of size (T, N, -1)
            obs_batch = [torch.stack(obs_batch_t, 1) for obs_batch_t in obs_batch]
            actions_batch = [torch.stack(actions_batch_t, 1) for actions_batch_t in actions_batch]
            value_preds_batch = torch.stack(value_preds_batch, 1)
            return_batch = torch.stack(return_batch, 1)
            masks_batch = torch.stack(masks_batch, 1)
            old_action_log_probs_batch = [torch.stack(
                old_action_log_probs_batch_t, 1) for old_action_log_probs_batch_t in old_action_log_probs_batch]
            adv_targ = torch.stack(adv_targ, 1)

            # States is just a (N, -1) tensor
            recurrent_hidden_states_batch = torch.stack(
                recurrent_hidden_states_batch, 1).view(N, -1)

            # Flatten the (T, N, ...) tensors to (T * N, ...)
            obs_batch = [_flatten_helper(T, N, obs_batch_t) for obs_batch_t in obs_batch]
            actions_batch = [_flatten_helper(T, N, actions_batch_t) for actions_batch_t in actions_batch]
            value_preds_batch = _flatten_helper(T, N, value_preds_batch)
            return_batch = _flatten_helper(T, N, return_batch)
            masks_batch = _flatten_helper(T, N, masks_batch)
            old_action_log_probs_batch = [_flatten_helper(T, N, \
                    old_action_log_probs_batch_t) for old_action_log_probs_batch_t in old_action_log_probs_batch]
            adv_targ = _flatten_helper(T, N, adv_targ)

            yield obs_batch, recurrent_hidden_states_batch, actions_batch, \
                    value_preds_batch, return_batch, masks_batch, old_action_log_probs_batch, adv_targ
