from torch.utils.data import Dataset
from torch.utils.data.dataloader import default_collate
import dgl
import torch
import ipdb

import glob
from tqdm import tqdm
import pickle as pkl
from utils import utils_rl_agent
from arguments import *
import yaml
import torch.nn.functional as F
import multiprocessing as mp
import numpy as np


class AgentTypeDataset(Dataset):
    def __init__(self, path_init, args_config, split='train', build_graphs_in_loader=False):
        self.path_init = path_init
        self.graph_helper = utils_rl_agent.GraphHelper(max_num_objects=args_config['model']['max_nodes'])
        self.get_edges = args_config['model']['state_encoder'] == 'GNN'
        # Build the agent types

        with open(self.path_init, 'rb+') as f:
            agent_files = pkl.load(f)
            agent_files = agent_files

        agent_type_max = max(agent_files.values())
        
        # clean the agent folder
        pkl_files = []
        labels = []
        agent_labels = [
            # full/partial, mem high, mem low, open high, open low, spiked/uniform
            [1, 0, 0, 0, 0, 0],
            [1, 0, 0, 1, 0, 0],
            [1, 0, 0, 0, 1, 0],
            [0, 1, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 1],
            [0, 0, 1, 0, 0, 0],
            [0, 0, 0, 0, 0, 1],
            [0, 0, 1, 0, 1, 0],
            [0, 0, 1, 1, 0, 0],
        ]
        if args_config['train']['agents'] == 'all':
            agents_use = list(range(agent_type_max+1))
        else:
            agents_use = [int(x) for x in args_config['train']['agents'].split(',')]

        for filename, label_agent in agent_files.items():
            if label_agent in agents_use:
                pkl_files.append(filename)
                labels.append(label_agent)


        self.max_labels = agent_type_max+1 
        self.labels = labels
        self.pkl_files = pkl_files
        self.overfit = args_config['train']['overfit']
        self.max_tsteps = args_config['model']['max_tsteps']
        self.max_actions = args_config['model']['max_actions']
        self.failed_items = mp.Array('i', len(self.pkl_files))
        self.args_config = args_config
        
        print("Loading data...")
        print("Filename: {}. Episodes: {}. Objects: {}".format(path_init, len(self.pkl_files), len(self.graph_helper.object_dict)))
        print("---------------")
        assert self.max_actions == len(self.graph_helper.action_dict)+1, '{} vs {}'.format(self.max_actions, len(self.graph_helper.action_dict))

    def __len__(self):
        return len(self.pkl_files)

    def failure(self, index):
        if index not in self.failed_items:
            self.failed_items[index] = 1
        return self.__getitem__(0)

    def get_failures(self):
        cont = [item for item in self.failed_items]
        return sum(cont)

    def __getitem__(self, index):
        if self.overfit:
            index = 0
        file_name = self.pkl_files[index]
        seed_number = int(file_name.split('.')[-2]) 
        with open(file_name, 'rb') as f:
            content = pkl.load(f)



        ##############################
        #### Inputs high level policy
        ##############################
        # Encode goal
        if 'action' not in content:
            print("FAil", self.pkl_files[index]) 
            return self.failure(index)
        


        goals = content['goals'][0]

        target_obj_class = [self.graph_helper.object_dict.get_id('no_obj')] * 6
        target_loc_class = [self.graph_helper.object_dict.get_id('no_obj')] * 6
        mask_goal_pred = [0.0] * 6

        pre_id = 0
        obj_pred_names, loc_pred_names = [], []

        id2node = {node['id']: node for node in content['graph'][0]['nodes']}
        for predicate, info in content['goals'][0].items():
            count = info
            if count == 0:
                continue

            # if not (predicate.startswith('on') or predicate.startswith('inside')):
            #     continue

            elements = predicate.split('_')
            obj_class_id = int(self.graph_helper.object_dict.get_id(elements[1]))
            loc_class_id = int(self.graph_helper.object_dict.get_id(id2node[int(elements[2])]['class_name']))

            obj_pred_names.append(elements[1])
            loc_pred_names.append(id2node[int(elements[2])]['class_name'])
            for _ in range(count):
                try:
                    target_obj_class[pre_id] = obj_class_id
                    target_loc_class[pre_id] = loc_class_id
                    mask_goal_pred[pre_id] = 1.0
                    pre_id += 1
                except:
                    pdb.set_trace()

        goal = {'target_loc_class': torch.tensor(target_loc_class), 
                'target_obj_class': torch.tensor(target_obj_class), 
                'mask_goal_pred': torch.tensor(mask_goal_pred)}



        label_one_hot = torch.tensor(self.labels[index])
        # print(content.keys())
        attributes_include = ['class_objects', 'states_objects', 'object_coords', 'mask_object', 'node_ids', 'mask_obs_node']
        if self.get_edges:
            attributes_include += ['edge_tuples', 'edge_classes', 'mask_edge']
        time_graph = {attr_name: [] for attr_name in attributes_include}
        # print(list(content.keys()))

        program = content['action'][0]
        if len(program) == 0:
            print(index)


        time_graph['mask_close'] = []
        time_graph['mask_goal'] = []

        for it, graph in enumerate(content['graph']):
            # if it == len(content['graph']) - 1:
            #     # Skip the last graph
            #     continue

            if it >= self.max_tsteps:
                break
            graph_info, _ = self.graph_helper.build_graph(graph, character_id=1, include_edges=self.get_edges, obs_ids=content['obs'][it])

            # class names
            for attribute_name in attributes_include:
                if attribute_name not in graph_info:
                    print(attribute_name, index, self.pkl_files[index])
                    return self.failure(index)
                time_graph[attribute_name].append(torch.tensor(graph_info[attribute_name]))

            # ipdb.set_trace()
            # Build closeness and goal mask
            close_rel_id = self.graph_helper.relation_dict.get_id('CLOSE')
            close_nodes = list(graph_info['edge_tuples'][graph_info['edge_classes'] == close_rel_id])
            
            mask_close = np.zeros(graph_info['class_objects'].shape)
            mask_goal = np.zeros(graph_info['class_objects'].shape) 

            # fill up the closeness mask
            if len(close_nodes) > 0:
                indexe = [int(edge[1]) for edge in close_nodes if edge[0] == 0]
                mask_close[np.array(indexe)] = 1.0

            # Fill up goal object mask
            goal_loc = [target_loc for it_pred, target_loc in enumerate(target_loc_class) if mask_goal_pred[it_pred] == 1]
            goal_obj = [target_obj for it_pred, target_obj in enumerate(target_obj_class) if mask_goal_pred[it_pred] == 1]
            goal_obs = list(set(goal_obj))
            for goal_id in goal_obs:
                mask_goal[graph_info['class_objects'] == goal_id] = 1.0

            time_graph['mask_close'].append(torch.tensor(mask_close))
            time_graph['mask_goal'].append(torch.tensor(mask_goal))
            # ipdb.set_trace()

        # Match graph indices to index in the tensor
        node_ids = graph_info['node_ids']
        indexgraph2ind = {node_id: idi for idi, node_id in enumerate(node_ids)}

        # We will start with a No-OP action
        program_batch = {
            'action': [self.max_actions - 1],
            'obj1': [-1],
            'obj2': [-1],
            'indobj1': [indexgraph2ind[-1]],
            'indobj2': [indexgraph2ind[-1]],
        }

        # We start at 1 to skip the first instruction
        for it, instr in enumerate(program):
            
            # we want to add an ending action
            if it >= self.max_tsteps - 1:
                break
            instr_item = self.graph_helper.actionstr2index(instr)
            program_batch['action'].append(instr_item[0])
            program_batch['obj1'].append(instr_item[1])
            program_batch['obj2'].append(instr_item[2])
            try:
                program_batch['indobj1'].append(indexgraph2ind[instr_item[1]])
                program_batch['indobj2'].append(indexgraph2ind[instr_item[2]])
            except:
                #print("Index", index, program, it)
                #ipdb.set_trace()
                return self.failure(index)

        program_batch['action'].append(self.max_actions - 1)
        program_batch['obj1'].append(-1)
        program_batch['obj2'].append(-1)
        program_batch['indobj1'].append(indexgraph2ind[-1])
        program_batch['indobj2'].append(indexgraph2ind[-1])

        num_tsteps = len(program_batch['action']) - 1
        for key in program_batch.keys():
            unpadded_tensor = torch.tensor(program_batch[key])

            # The program has an extra step
            padding_amount = self.max_tsteps - num_tsteps
            padding = [0] * unpadded_tensor.dim() * 2
            padding[-1] = padding_amount
            tuple_pad = tuple(padding)
            program_batch[key] = F.pad(unpadded_tensor, pad=tuple_pad, mode='constant', value=0.)

        length_mask = torch.zeros(self.max_tsteps)
        length_mask[:num_tsteps] = 1.

        # Batch across time
        for attribute_name in time_graph.keys():
            unpadded_tensor = torch.cat([item[None, :] for item in time_graph[attribute_name]]).float()
            # Do padding
            padding_amount = self.max_tsteps - num_tsteps
            # ipdb.set_trace()
            padding = [0] * unpadded_tensor.dim() * 2
            padding[-1] = padding_amount
            tuple_pad = tuple(padding)
            time_graph[attribute_name] = F.pad(unpadded_tensor, pad=tuple_pad, mode='constant', value=0.)
            # if time_graph[attribute_name].shape[0] > self.max_tsteps:
            #     print(self.max_tsteps, num_tsteps, len(content['graph']), unpadded_tensor.shape[0])
        
        # for attribute in program_graph.keys():
        #     print(attribute, program_graph[attribute].shape)
        # print('----')



        label_agent = seed_number + self.labels[index] * 5
        real_label = self.labels[index]

        if self.args_config['model']['state_encoder'] == 'GNN' and build_graphs_in_loader:
            time_graph['graph'] = build_graph(time_graph)
        return time_graph, program_batch, label_one_hot, length_mask, goal, label_agent, real_label

def build_graph(time_graph):
    graphs = []
    tsteps = len(time_graph['mask_object'])
    for t in range(tsteps):
        g = dgl.DGLGraph()
        num_nodes = time_graph['mask_object'][t].sum()
        num_edges = int(time_graph['mask_edge'][t].sum())
        edge_tuples = time_graph['edge_tuples'][t]
        edge_classes = time_graph['edge_classes'][t]
        g.add_nodes(num_nodes)
        g.add_edges(edge_tuples[:num_edges, 0].long(), edge_tuples[:num_edges, 1].long(), 
                {'rel_type': edge_classes[:num_edges]})
        graphs.append(g)
    return graphs

def collate_fn(inputs):
    new_inputs = []
    for i in range(1, len(inputs[0])):
        new_inputs.append(default_collate([inp[i] for inp in inputs]))
    
    first_inp = {}
    for key in inputs[0][0].keys():
        if key not in ['graph', 'edge_tuples', 'edge_classes', 'mask_edge']:
            first_inp[key] = default_collate([inp[0][key] for inp in inputs])

    #ipdb.set_trace()
    graph_list = [inp[0]['graph'] for inp in inputs]
    graph_list = [graph for graphs in graph_list for graph in graphs]
    #print(type(graph_list[0]))
    first_inp['graph'] = dgl.batch(graph_list)
    new_inputs = [first_inp] + new_inputs
    return new_inputs

if __name__ == '__main__':
    arguments = get_args_pref_agent()
    with open(arguments.config, 'r') as f:
        config = yaml.load(f)
    dataset = AgentTypeDataset(path_init='../dataset/dataset_agent_model_v0_train.pkl', args_config=config)
    data = dataset[0]
    # ipdb.set_trace()
