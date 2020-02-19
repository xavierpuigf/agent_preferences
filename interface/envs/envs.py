import numpy as np
import cv2
from PIL import ImageFont, ImageDraw, Image

import time
import logging
import atexit
from sys import platform
import subprocess
import os
import glob

import pdb
import random
import torch
import torchvision
import vh_graph
from vh_graph.envs import belief, vh_env
from simulation.unity_simulator import comm_unity as comm_unity

from agents import MCTS_agent, PG_agent
from gym import spaces, envs
import ipdb
from profilehooks import profile

import utils_rl_agent
logger = logging.getLogger("mlagents_envs")

class UnityEnvWrapper:
    def __init__(self, env_id, env_copy_id, file_name='../../executables/exec_linux02.10.x86_64', base_port=8080, num_agents=1):
        atexit.register(self.close)
        self.port_number = base_port + env_copy_id 
        print(self.port_number)
        self.proc = None
        self.timeout_wait = 60
        self.file_name = file_name
        #self.launch_env(file_name)


        # TODO: get rid of this, should be notfiied somehow else
        
        self.comm = comm_unity.UnityCommunication(port=str(self.port_number))

        print('Checking connection')
        self.comm.check_connection()


        
        self.num_agents = num_agents
        self.graph = None
        self.recording = False
        self.follow = False
        self.num_camera_per_agent = 6
        self.CAMERA_NUM = 1 # 0 TOP, 1 FRONT, 2 LEFT..
        
        self.comm.reset(env_id)
# Assumption, over initializing the env wrapper, we only use one enviroment id
        # TODO: make sure this is true
        _, graph = self.comm.environment_graph()
        self.rooms = [(node['class_name'], node['id']) for node in graph['nodes'] if node['category'] == 'Rooms']
        self.id2node = {node['id']: node for node in graph['nodes']}
        self.offset_cameras = self.comm.camera_count()[1]
        characters = ['Chars/Male1', 'Chars/Female1']
        for i in range(self.num_agents):
            self.comm.add_character(characters[i])
        #comm.render_script(['<char0> [walk] <kitchentable> (225)'], camera_mode=False, gen_vid=False)
        #comm.render_script(['<char1> [walk] <bathroom> (11)'], camera_mode=False, gen_vid=False)  
        if self.follow:
            if self.recording:
                comm.render_script(['<char0> [walk] <kitchentable> (225)'], recording=True, gen_vid=False, camera_mode='FIRST_PERSON')
            else:
                comm.render_script(['<char0> [walk] <kitchentable> (225)'], camera_mode=False, gen_vid=False)

        self.get_graph()
        #self.test_prep()
   
    def returncode_to_signal_name(returncode: int):
        """
        Try to convert return codes into their corresponding signal name.
        E.g. returncode_to_signal_name(-2) -> "SIGINT"
        """
        try:
            # A negative value -N indicates that the child was terminated by signal N (POSIX only).
            s = signal.Signals(-returncode)  # pylint: disable=no-member
            return s.name
        except Exception:
            # Should generally be a ValueError, but catch everything just in case.
            return None

    def close(self):
        if self.proc is not None:
            self.proc.kill()
            self.proc = None
        return
        if self.proc is not None:
            # Wait a bit for the process to shutdown, but kill it if it takes too long
            try:
                self.proc.wait(timeout=self.timeout_wait)
                signal_name = self.returncode_to_signal_name(self.proc.returncode)
                signal_name = f" ({signal_name})" if signal_name else ""
                return_info = f"Environment shut down with return code {self.proc.returncode}{signal_name}."
                logger.info(return_info)
            except subprocess.TimeoutExpired:
                logger.info("Environment timed out shutting down. Killing...")
            # Set to None so we don't try to close multiple times.
            self.proc = None

    def launch_env(self, file_name, args=''):
        # based on https://github.com/Unity-Technologies/ml-agents/blob/bf12f063043e5faf4b1df567b978bb18dcb3e716/ml-agents/mlagents/trainers/learn.py
        cwd = os.getcwd()
        file_name = (
            file_name.strip()
            .replace(".app", "")
            .replace(".exe", "")
            .replace(".x86_64", "")
            .replace(".x86", "")
        )
        true_filename = os.path.basename(os.path.normpath(file_name))
        print(file_name)
        logger.debug("The true file name is {}".format(true_filename))
        launch_string = None
        if platform == "linux" or platform == "linux2":
            candidates = glob.glob(os.path.join(cwd, file_name) + ".x86_64")
            if len(candidates) == 0:
                candidates = glob.glob(os.path.join(cwd, file_name) + ".x86")
            if len(candidates) == 0:
                candidates = glob.glob(file_name + ".x86_64")
            if len(candidates) == 0:
                candidates = glob.glob(file_name + ".x86")
            if len(candidates) > 0:
                launch_string = candidates[0]

        elif platform == "darwin":
            candidates = glob.glob(
                os.path.join(
                    cwd, file_name + ".app", "Contents", "MacOS", true_filename
                )
            )
            if len(candidates) == 0:
                candidates = glob.glob(
                    os.path.join(file_name + ".app", "Contents", "MacOS", true_filename)
                )
            if len(candidates) == 0:
                candidates = glob.glob(
                    os.path.join(cwd, file_name + ".app", "Contents", "MacOS", "*")
                )
            if len(candidates) == 0:
                candidates = glob.glob(
                    os.path.join(file_name + ".app", "Contents", "MacOS", "*")
                )
            if len(candidates) > 0:
                launch_string = candidates[0]

        if launch_string is None:
            self.close()
            raise Exception(
                "Couldn't launch the {0} environment. "
                "Provided filename does not match any environments.".format(
                    true_filename
                )
            )
        else:
            docker_training = False
            if not docker_training:
                subprocess_args = [launch_string]
                #subprocess_args += ["-batchmode"]
                #subprocess_args += ["-http-port="+str(self.port_number)]
                subprocess_args += args
                try:
                    self.proc = subprocess.Popen(
                            subprocess_args, 
                            start_new_session=True)
                    ret_val = self.proc.poll()
                except:
                    raise Exception('Error, environment was found but could not be launched')
            else:
                raise Exception("Docker training is still not implemented")

        pass

    def get_graph(self):

        _, self.graph = self.comm.environment_graph()
        return self.graph

    # TODO: put in some utils
    def world2im(self, camera_data, wcoords):
        wcoords = wcoords.transpose()
        proj = np.array(camera_data['projection_matrix']).reshape((4,4)).transpose()
        w2cam = np.array(camera_data['world_to_camera_matrix']).reshape((4,4)).transpose()
        cw = np.concatenate([wcoords, np.ones((1, wcoords.shape[1]))], 0) # 4 x N
        pixelcoords = np.matmul(proj, np.matmul(w2cam, cw)) # 4 x N
        pixelcoords = pixelcoords/pixelcoords[-1, :]
        pixelcoords = (pixelcoords + 1)/2.
        pixelcoords[1,:] = 1. - pixelcoords[1, :]
        return pixelcoords[:2, :]

    def get_visible_objects(self):
        camera_ids = [self.offset_cameras+i*self.num_camera_per_agent+self.CAMERA_NUM for i in range(self.num_agents)]
        object_ids = [int(idi) for idi in self.comm.get_visible_objects(camera_ids)[1].keys()]
        _, cam_data = self.comm.camera_data(camera_ids)
        _, graph = self.comm.environment_graph()
        object_position = np.array(
                [node['bounding_box']['center'] for node in graph['nodes'] if node['id'] in object_ids])
        obj_pos = self.world2im(cam_data[0], object_position) 
        return object_ids, obj_pos

    def get_observations(self, mode='normal', image_width=128, image_height=128):
        camera_ids = [self.offset_cameras+i*self.num_camera_per_agent+self.CAMERA_NUM for i in range(self.num_agents)]
        s, images = self.comm.camera_image(camera_ids, mode=mode, image_width=image_width, image_height=image_height)
        #images = [image[:,:,::-1] for image in images]
        return images

    def test_prep(self):
        node_id_new = 2007
        s, graph = self.comm.environment_graph()
        table_id = [node['id'] for node in graph['nodes'] if node['class_name'] == 'kitchentable'][0]
        container_id = [node['id'] for node in graph['nodes'] if node['class_name'] in ['fridge', 'freezer']][0]
        drawer_id = [node['id'] for node in graph['nodes'] if node['class_name'] in ['kitchencabinets']][0]


        id2node = {node['id']: node for node in graph['nodes']}

        # plates = [edge['from_id'] for edge in graph['edges'] if edge['to_id'] == table_id and id2node[edge['from_id']]['class_name'] == 'plate']
        # graph['edges'] = [edge for edge in graph['edges'] if edge['from_id'] not in plates and edge['to_id'] not in plates]
        # edge_plates = [{'from_id': plate_id, 'to_id': drawer_id, 'relation_type': 'INSIDE'} for plate_id in plates] 
        # graph['edges'] += edge_plates
        #self.comm.render_script(['<char0> [walk] <livingroom> (319)'], image_synthesis=[]).set_trace()


        new_node = {'id': node_id_new, 'class_name': 'glass', 'states': [], 'properties': ['GRABBABLE']}
        new_edge = {'from_id': node_id_new, 'relation_type': 'INSIDE', 'to_id': container_id}
        graph['nodes'].append(new_node)
        graph['edges'].append(new_edge)
        success = self.comm.expand_scene(graph)
        print(success)

    def agent_ids(self):
        return sorted([x['id'] for x in self.graph['nodes'] if x['class_name'] == 'character'])

    
    def execute(self, actions): # dictionary from agent to action
        # Get object to interact with

        # This solution only works for 2 agents, we can scale it for more agents later

        agent_do = list(actions.keys())
        if self.follow:
            actions[0] = '[walk] <character> (438)'
        if len(actions.keys()) > 1:
            if sum(['walk' in x for x in actions.values()]) == 0:
                #continue
                objects_interaction = [x.split('(')[1].split(')')[0] for x in actions.values()]
                if len(set(objects_interaction)) == 1:
                    agent_do = [1] # [random.choice([0,1])]

        script_list = ['']
        for agent_id in agent_do:
            script = actions[agent_id]
            current_script = ['<char{}> {}'.format(agent_id, script)]
            

            script_list = [x+ '|' +y if len(x) > 0 else y for x,y in zip (script_list, current_script)]

        #if self.follow:
        script_list = [x.replace('walk', 'walktowards') for x in script_list]
        # script_all = script_list
        if self.recording:
            success, message = self.comm.render_script(script_list, recording=True, gen_vid=False, camera_mode='FIRST_PERSON')
        else:
            success, message = self.comm.render_script(script_list, recording=False, gen_vid=False)
        if not success:
            ipdb.set_trace()
        result = {}
        for agent_id in agent_do:
            result[agent_id] = (success, message) 

        return result




class UnityEnv:
    def __init__(self, num_agents=2, seed=0, env_id=0, env_copy_id=0, observation_type='coords'):
        self.env_name = 'virtualhome'
        self.num_agents = num_agents
        self.env = vh_env.VhGraphEnv(n_chars=self.num_agents)
        self.env_id = env_id

        self.unity_simulator = UnityEnvWrapper(int(env_id), int(env_copy_id), num_agents=self.num_agents)    
        self.agent_ids =  self.unity_simulator.agent_ids()
        self.agents = {}


        self.system_agent_id = self.agent_ids[0]

        if self.num_agents>1:
            self.my_agent_id = self.agent_ids[1]

        self.add_system_agent()

        self.actions = {}
        self.actions['system_agent'] = []
        self.actions['my_agent'] = []
        self.image_width = 224
        self.image_height = 224
        self.graph_helper = utils_rl_agent.GraphHelper()


        ## ------------------------------------------------------------------------------------        
        self.observation_type = observation_type # Image, Coords
        self.viewer = None
        self.num_objects = 100
        self.num_actions = 9
        self.action_space = spaces.Tuple((spaces.Discrete(self.num_actions), spaces.Discrete(self.num_objects), spaces.Discrete(self.num_objects)))
        if self.observation_type == 'coords':

            # current_obs = [current_obs, node_names, node_states, edges, edge_types, mask_nodes, mask_edges, 
            #           rel_coords, position_objects, mask]
            self.observation_space = spaces.Tuple((
                # Image
                spaces.Box(low=0, high=255., shape=(3, self.image_height, self.image_width)), 
                # Graph
                #utils_rl_agent.GraphSpace(),

                spaces.Box(low=0, high=self.graph_helper.num_classes, shape=(self.graph_helper.num_objects, )), 
                spaces.Box(low=0, high=1., shape=(self.graph_helper.num_objects, self.graph_helper.num_states)), 
                spaces.Box(low=0, high=self.graph_helper.num_objects, shape=(self.graph_helper.num_edges, 2)), 
                spaces.Box(low=0, high=self.graph_helper.num_edge_types, shape=(self.graph_helper.num_edges, )),
                spaces.Box(low=0, high=1, shape=(self.graph_helper.num_objects, )), 
                spaces.Box(low=0, high=1, shape=(self.graph_helper.num_edges, )), 

                # Target object
                spaces.Box(low=-100, high=100, shape=(2,)),
                spaces.Box(low=0, high=max(self.image_height, self.image_width), 
                           shape=(self.num_objects, 2)), # 2D coords of the objects
                spaces.Box(low=0, high=1, 
                    shape=(self.num_objects, ))))
        else:
            self.observation_space = spaces.Box(low=0, high=255., shape=(3, self.image_height, self.image_width))
        self.reward_range = (-10, 50.)
        self.metadata = {'render.modes': ['human']}
        self.spec = envs.registration.EnvSpec('virtualhome-v0')

        
        self.history_observations = []
        self.len_hist = 4
        self.num_steps = 0
        self.prev_dist = None

        self.micro_id = -1

    def seed(self, seed):
        pass

    def close(self):
        self.unity_simulator.close()

    def compute_toy_reward(self):
        dist = self.get_distance()
        if self.prev_dist is None:
            self.prev_dist = dist

        reward = self.prev_dist - dist - 0.5
        self.prev_dist = dist
        is_done = dist < 1.5
        if is_done:
            reward += 10
        info = {'dist': dist, 'done': is_done, 'reward': reward}
        return reward, info
    

    def get_distance(self, norm=None):
        s, gr = self.unity_simulator.comm.environment_graph()
        char_node = [node['bounding_box']['center'] for node in gr['nodes'] if node['class_name'] == 'character'][0]
        micro_node = [node['bounding_box']['center'] for node in gr['nodes'] if node['class_name'] == 'microwave'][0]
        micro_node_id = [node['id'] for node in gr['nodes'] if node['class_name'] == 'microwave'][0]
        self.micro_id = micro_node_id
        if norm == 'no':
            return np.array(char_node) - np.array(micro_node)
        dist = np.linalg.norm(np.array(char_node) - np.array(micro_node), norm)
        return dist

    def render(self, mode='human'):
        obs, img = self.get_observations(mode='normal', image_width=256, image_height=256)
        im_pil = Image.fromarray(img)
        draw = ImageDraw.Draw(im_pil)
        # Choose a font
        font = ImageFont.truetype("Roboto-Regular.ttf", 20)
        reward, info = self.compute_toy_reward()

        # Draw the text
        draw.text((0, 0), "dist: {:.3f}".format(info['reward']), font=font)
        img = cv2.cvtColor(np.array(im_pil), cv2.COLOR_RGB2BGR)


        distance = info['dist']
        if mode == 'rgb_array':
            return image
        elif mode == 'human':
            from gym.envs.classic_control import rendering
            if self.viewer is None:
                self.viewer = rendering.SimpleImageViewer(maxwidth=500)
            self.viewer.imshow(img)
            return self.viewer.isopen
         

    def reset(self, graph=None, task_goal=None):
        # reset system agent
        # #self.agents[self.system_agent_id].reset(graph, task_goal, seed=self.system_agent_id)
        # #self.history_observations = [torch.zeros(1, 84, 84) for _ in range(self.len_hist)]
        if graph is None:
            self.unity_simulator.comm.fast_reset(self.env_id)
        # #self.unity_simulator.comm.add_character()
        # #self.unity_simulator.comm.render_script(['<char0> [walk] <kitchentable> (225)'], gen_vid=False, recording=True)
        
        if task_goal is not None:
            self.goal_spec = task_goal[self.system_agent_id]
            self.agents[self.system_agent_id].reset(graph, task_goal, seed=self.system_agent_id)
        self.prev_dist = self.get_distance()
        obs = self.get_observations()[0]
        self.num_steps = 0
        return obs

    def obtain_actions(self, graph):
        actions = ['turnleft', 'walkforward', 'turnright', 'walktowards', 'open', 'close', 'putback', 'putin', 'grab'] 
        objects = [(None, None)] + self.unity_simulator.rooms + [(self.unity_simulator.id2node[id_obj]['class_name'], id_obj) for id_obj in self.unity_simulator.get_visible_objects()[0]]
        objects2 = objects
        return actions, objects, objects2


    def step(self, my_agent_action):
        #actions = ['<char0> [walktowards] <microwave> ({})'.format(self.micro_id), '<char0> [turnleft]', '<char0> [turnright]']
        _, current_graph = self.unity_simulator.comm.environment_graph()
        actions, objects1, objects2 = self.obtain_actions(current_graph)
        if len(actions) < self.num_actions:
            actions = actions + [None] * (self.num_actions - len(actions))

        if len(objects1) < self.num_objects:
            objects1 = objects1 + [None] * (self.num_objects - len(objects1))

        if len(objects2) < self.num_objects:
            objects2 = objects2 + [None] * (self.num_objects - len(objects2))
        pdb.set_trace()
        action = actions[my_agent_action[0][0]]
        (o1, o1_id) = objects1[my_agent_action[1][0]]
        (o2, o2_id) = objects2[my_agent_action[2][0]]
        
        #action_str = actions[my_agent_action]
        obj1_str = '' if o1 is None else '<o1> (o1_id)' 
        obj2_str = '' if o1 is None else '<o2> (o2_id)' 
        action_str = f'<char0> [{action}] {obj1_str} {obj2_str}'.strip()
        self.unity_simulator.comm.render_script([action_str], recording=False, gen_vid=False)
        self.num_steps += 1
        obs, _ = self.get_observations()
        reward, info = self.compute_toy_reward() 
        reward = torch.Tensor([reward])
        done = info['done']
        if self.num_steps > 40:
            done = True
        done = np.array([done])
        infos = {}
        #if done:
        #    obs = self.reset()
        return obs, reward, done, infos


    def add_system_agent(self):
        ## Alice model
        self.agents[self.system_agent_id] = MCTS_agent(unity_env=self,
                               agent_id=self.system_agent_id,
                               char_index=0,
                               max_episode_length=5,
                               num_simulation=100,
                               max_rollout_steps=3,
                               c_init=0.1,
                               c_base=1000000,
                               num_samples=1,
                               num_processes=1)

    def get_system_agent_action(self, task_goal, last_action, last_subgoal, opponent_subgoal=None):
        self.agents[self.system_agent_id].sample_belief(self.env.get_observations(char_index=0))
        self.agents[self.system_agent_id].sim_env.reset(self.agents[self.system_agent_id].previous_belief_graph, task_goal)
        action, info = self.agents[self.system_agent_id].get_action(task_goal[0], last_action, last_subgoal, opponent_subgoal)

        if action is None:
            print("system agent action is None! DONE!")
            pdb.set_trace()
        # else:
        #     print(action, info['plan'])

        return action, info

    def get_all_agent_id(self):
        return self.agent_ids

    def get_my_agent_id(self):
        if self.num_agents==1:
            error("you haven't set your agent")
        return self.my_agent_id





    def get_graph(self):
        graph = self.unity_simulator.get_graph()
        graph = self.inside_not_trans(graph)
        return graph

    def get_system_agent_observations(self, modality=['rgb_image']):
        observation = self.agents[self.system_agent_id].num_cameras = self.unity_simulator.camera_image(self.system_agent_id, modality)
        return observation

    def get_my_agent_observations(self, modality=['rgb_image']):
        observation = self.agents[self.system_agent_id].num_cameras = self.unity_simulator.camera_image(self.my_agent_id, modality)
        return observation


    def inside_not_trans(self, graph):
        inside_node = {}
        other_edges = []
        for edge in graph['edges']:
            if edge['relation_type'] == 'INSIDE':
                if edge['from_id'] not in inside_node:
                    inside_node[edge['from_id']] = []
                inside_node[edge['from_id']].append(edge['to_id'])
            else:
                other_edges.append(edge)
        # Make sure we make trasnsitive first
        inside_trans = {}
        def inside_recursive(curr_node_id):
            if curr_node_id in inside_trans:
                return inside_trans[node_id]
            if curr_node_id not in inside_node.keys():
                return []
            else:
                all_parents = []
                for node_id_parent in inside_node[curr_node_id]:
                    curr_parents = inside_recursive(node_id_parent)
                    all_parents += curr_parents

                if len(all_parents) > 0:
                    inside_trans[curr_node_id] = list(set(all_parents))
                return all_parents

        for node_id in inside_node.keys():
            if len(inside_node[node_id]) > 1:
                inside_recursive(node_id)
            else:
                other_edges.append({'from_id':node_id, 'relation_type': 'INSIDE', 'to_id': inside_node[node_id][0]})

        num_parents = {}
        for node in graph['nodes']:
            if node['id'] not in inside_trans.keys():
                num_parents[node['id']] = 0
            else:
                num_parents[node['id']] = len(inside_trans[node['id']])

        edges_inside = []
        for node_id, nodes_inside in inside_trans.items():
            all_num_parents = [num_parents[id_n] for id_n in nodes_inside]
            max_np = max(all_num_parents)
            node_select = [node_inside[i] for i, np in enumerate(all_num_parents) if np == max_np][0]
            edges_inside.append({'from_id':node_id, 'relation_type': 'INSIDE', 'to_id': node_select})
        graph['edges'] = edges_inside + other_edges
        return graph
   
    def get_observations(self, mode='seg_class', image_width=None, image_height=None):
        if image_height is None:
            image_height = self.image_height
        if image_width is None:
            image_width = self.image_width
        images = self.unity_simulator.get_observations(mode=mode, image_width=image_width, image_height=image_height)
        current_obs = images[0]
        current_obs = torchvision.transforms.functional.to_tensor(current_obs)[None, :]
        graph = self.unity_simulator.get_graph()

        distance = self.get_distance(norm='no')
        rel_coords = torch.Tensor(list([distance[0], distance[2]]))[None, :]
        visible_objects, position_objects = self.unity_simulator.get_visible_objects()
        position_objects = position_objects.transpose()
        position_objects_tensor = np.zeros((self.num_objects, 2))
        mask = np.zeros((self.num_objects))
        position_objects_tensor[:position_objects.shape[0], :] = position_objects
        mask[:position_objects.shape[0]] = 1
        position_objects = torch.Tensor(position_objects_tensor)[None, :]
        mask = torch.Tensor(mask)[None, :]
    
        graph_inputs = list(self.graph_helper.build_graph(graph, visible_objects))
        #rel_coords = torch.Tensor(position_objects)[None, :]
        current_obs = [current_obs] + graph_inputs + [rel_coords, position_objects, mask]
        return current_obs, images[0]

    def print_action(self, system_agent_action, my_agent_action):
        self.actions['system_agent'].append(system_agent_action)
        self.actions['my_agent'].append(my_agent_action)

        system_agent_actions = self.actions['system_agent']
        my_agent_actions = self.actions['my_agent']
        num_steps = len(system_agent_actions)

        print('**************************************************************************')
        if self.num_agents>1:
            for i in range(num_steps):
                print('step %04d:\t|"system": %s \t\t\t\t\t\t |"my_agent": %s' % (i+1, system_agent_actions[i].ljust(30), my_agent_actions[i]))
        else:
            for i in range(num_steps):
                print('step %04d:\t|"system": %s' % (i+1, system_agent_actions[i]))

        print('**************************************************************************')
