#!/usr/bin/env python3
import ptan
import gym
import argparse
import random
import torch
import numpy as np
import torch.optim as optim
import torch.nn.functional as F

from ignite.engine import Engine
from types import SimpleNamespace
from lib import common, ppo, dqn_extra


HYPERPARAMS = {
    'debug': SimpleNamespace(**{
        'env_name':         "CartPole-v0",
        'stop_reward':      190.0,
        'run_name':         'debug',
        'actor_lr':         1e-4,
        'critic_lr':        1e-4,
        'gamma':            0.9,
        'ppo_trajectory':   2049,
        'ppo_epoches':      10,
        'ppo_eps':          0.2,
        'batch_size':       32,
        'gae_lambda':       0.95,
        'entropy_beta':     0.1,
    }),
    'ppo': SimpleNamespace(**{
        'env_name':         "MountainCar-v0",
        'stop_reward':      -120.0,
        'run_name':         'ppo',
        'learning_rate':    1e-4,
        'gamma':            0.99,
        'ppo_trajectory':   2049,
        'ppo_epoches':      10,
        'ppo_eps':          0.2,
        'batch_size':       32,
        'gae_lambda':       0.95,
        'entropy_beta':     0.1,
    }),
    'noisynets': SimpleNamespace(**{
        'env_name':         "MountainCar-v0",
        'stop_reward':      -120.0,
        'run_name':         'noisynets',
        'learning_rate':    1e-5,
        'gamma':            0.99,
        'ppo_trajectory':   2049,
        'ppo_epoches':      10,
        'ppo_eps':          0.2,
        'batch_size':       32,
        'gae_lambda':       0.95,
        'entropy_beta':     0.1,
    }),
    'counts': SimpleNamespace(**{
        'env_name':         "MountainCar-v0",
        'stop_reward':      -20.0,
        'run_name':         'counts',
        'learning_rate':    1e-4,
        'gamma':            0.99,
        'ppo_trajectory':   2049,
        'ppo_epoches':      10,
        'ppo_eps':          0.2,
        'batch_size':       32,
        'gae_lambda':       0.95,
        'entropy_beta':     0.1,
        'counts_reward_scale': 0.5,
    }),
}


def counts_hash(obs):
    r = obs.tolist()
    return tuple(map(lambda v: round(v, 3), r))


if __name__ == "__main__":
    random.seed(common.SEED)
    torch.manual_seed(common.SEED)
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--name", required=True, help="Run name")
    parser.add_argument("-p", "--params", default='debug', help="Parameters, default=ppo")
    args = parser.parse_args()
    params = HYPERPARAMS[args.params]

    env = gym.make(params.env_name)
    if args.params == 'counts':
        env = dqn_extra.PseudoCountRewardWrapper(env, reward_scale=params.counts_reward_scale, hash_function=counts_hash)
    env.seed(common.SEED)

    if args.params == 'noisynets':
        net = ppo.MountainCarNoisyNetsPPO(env.observation_space.shape[0], env.action_space.n)
    else:
        net = ppo.MountainCarBasePPO(env.observation_space.shape[0], env.action_space.n)
    print(net)

    agent = ptan.agent.PolicyAgent(net.actor, apply_softmax=True, preprocessor=ptan.agent.float32_preprocessor)
    exp_source = ptan.experience.ExperienceSource(env, agent, steps_count=1)
    opt_actor = optim.Adam(net.actor.parameters(), lr=params.actor_lr)
    opt_critic = optim.Adam(net.critic.parameters(), lr=params.critic_lr)

    def process_batch(engine, batch):
        states_t, actions_t, adv_t, ref_t, old_logprob_t, final_states = batch

        final_vals_t = net.critic(torch.FloatTensor(final_states))
        final_vals = torch.mean(final_vals_t).item()

        opt_critic.zero_grad()
        value_t = net.critic(states_t)
        loss_value_t = F.mse_loss(value_t.squeeze(-1), ref_t)
        loss_value_t.backward()
        opt_critic.step()

        opt_actor.zero_grad()
        policy_t = net.actor(states_t)
        logpolicy_t = F.log_softmax(policy_t, dim=1)

        prob_t = F.softmax(policy_t, dim=1)
        loss_entropy_t = (prob_t * logpolicy_t).sum(dim=1).mean()

        logprob_t = logpolicy_t.gather(1, actions_t.unsqueeze(-1)).squeeze(-1)
        ratio_t = torch.exp(logprob_t - old_logprob_t)
        surr_obj_t = adv_t * ratio_t
        clipped_surr_t = adv_t * torch.clamp(ratio_t, 1.0 - params.ppo_eps, 1.0 + params.ppo_eps)
        loss_policy_t = -torch.min(surr_obj_t, clipped_surr_t).mean()
        loss_polent_t = params.entropy_beta * loss_entropy_t + loss_policy_t
        loss_polent_t.backward()
        opt_actor.step()

        res = {
            "loss": loss_value_t.item() + loss_polent_t.item(),
            "loss_value": loss_value_t.item(),
            "loss_policy": loss_policy_t.item(),
            "adv": adv_t.mean().item(),
            "ref": ref_t.mean().item(),
            "loss_entropy": loss_entropy_t.item(),
            "final_vals": final_vals,
        }
        return res



    engine = Engine(process_batch)
    common.setup_ignite(engine, params, exp_source, args.name)
    engine.run(ppo.batch_generator(exp_source, net.critic, net.actor, params.ppo_trajectory,
                                   params.ppo_epoches, params.batch_size,
                                   params.gamma, params.gae_lambda))
