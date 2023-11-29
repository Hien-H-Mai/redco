#  Copyright 2021 Google LLC
#  #
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  #
#      https://www.apache.org/licenses/LICENSE-2.0
#  #
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import fire
from pettingzoo import mpe
from PIL import Image

from maddpg_agent import MADDPGAgent


def main(init_params_path, env_name='simple_adversary_v3', n_episodes=10):
    env = getattr(mpe, env_name).parallel_env(render_mode='rgb_array')
    env.reset()

    state_dims = {
        agent: env.observation_space(agent=agent).shape[0]
        for agent in env.agents
    }
    action_dims = \
        {agent: env.action_space(agent=agent).n for agent in env.agents}

    maddpg = MADDPGAgent(
        agents=env.agents,
        state_dims=state_dims,
        action_dims=action_dims,
        init_params_path=init_params_path)

    frame_list = []
    for episode_idx in range(n_episodes):
        state, _ = env.reset()
        sum_rewards = {agent: 0. for agent in env.agents}

        while env.agents:
            action = {
                agent: maddpg.predict_action(
                    agent=agent, agent_state=state[agent], explore_eps=0.)
                for agent in env.agents
            }
            next_state, reward, done, _, _ = env.step(action)
            frame_list.append(Image.fromarray(env.render()))

            sum_rewards = {
                agent: sum_rewards[agent] + reward[agent]
                for agent in sum_rewards.keys()
            }

            state = next_state

        print(f'Episode {episode_idx} Reward: {sum_rewards}')

    env.close()

    frame_list[0].save('xx.gif', save_all=True, append_images=frame_list[1:], duration=1, loop=0)


if __name__ == '__main__':
    fire.Fire(main)
