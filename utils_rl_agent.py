from utils import DictObjId
from gym import spaces, envs
from dgl import DGLGraph
import numpy as np
import os
import json

class GraphHelper():
    def __init__(self):
        self.states = ['on', 'open', 'off', 'closed']
        self.relations = ['inside', 'close', 'facing', 'on']
        self.objects = self.get_objects()
        rooms = ['bathroom', 'bedroom', 'kitchen', 'livingroom']
        self.object_dict = DictObjId(self.objects + ['character'] + rooms + ['no_obj'])
        self.relation_dict = DictObjId(self.relations)
        self.state_dict = DictObjId(self.states)

        self.num_objects = 100
        self.num_edges = 200 
        self.num_edge_types = len(self.relation_dict)
        self.num_classes = len(self.object_dict)
        self.num_states = len(self.state_dict)

    def get_objects(self):
        dir_path = os.path.dirname(os.path.realpath(__file__))

        with open(f'{dir_path}/dataset/object_info.json', 'r') as f:
            content = json.load(f)
        objects = []
        for obj in content.values():
            objects += obj
        return objects
    
    def one_hot(self, states):
        one_hot = np.zeros(len(self.state_dict))
        for state in states:
            one_hot[self.state_dict.get_id(state)] = 1
        return one_hot

    def build_graph(self, graph, ids, plot_graph=False):
        ids = [node['id'] for node in graph['nodes'] if node['category'] == 'Rooms'] + ids
        id2node = {node['id']: node for node in graph['nodes']}
        # Character is always the first one
        ids = [node['id'] for node in graph['nodes'] if node['class_name'] == 'character'] + ids
        max_nodes = self.num_objects
        max_edges = self.num_edges
        edges = [edge for edge in graph['edges'] if edge['from_id'] in ids and edge['to_id'] in ids]
        nodes = [id2node[idi] for idi in ids]
        id2index = {node['id']: it for it, node in enumerate(nodes)}

        class_names_str = [node['class_name'] for node in nodes]
        class_names = np.array([self.object_dict.get_id(class_name) for class_name in class_names_str])
        node_states = np.array([self.one_hot(node['states']) for node in nodes])

        edge_types = np.array([self.relation_dict.get_id(edge['relation_type']) for edge in edges])

        if len(edges) > 0:
            edge_ids = np.concatenate(
                    [np.array([
                        id2index[edge['from_id']], 
                        id2index[edge['to_id']]])[None, :] for edge in edges], axis=0)

        else:
            pdb.set_trace()

        mask_edges = np.zeros(max_edges)
        all_edge_ids = np.zeros((max_edges, 2))
        all_edge_types = np.zeros((max_edges))

        mask_nodes = np.zeros((max_nodes))
        all_class_names = np.zeros((max_nodes))
        all_node_states = np.zeros((max_nodes, len(self.state_dict)))
        
        if len(edges) > 0:
            mask_edges[:len(edges)] = 1.
            all_edge_ids[:len(edges), :] = edge_ids
            all_edge_types[:len(edges)] = edge_types

        mask_nodes[:len(nodes)] = 1.
        all_class_names[:len(nodes)] = class_names
        all_node_states[:len(nodes)] = node_states

        
        if plot_graph:
            graph_viz = DGLGraph()
            graph_viz.add_nodes(len(nodes), {'names': class_names})
            labeldict =  {it: class_str for it, class_str in enumerate(class_names_str)}
        else:
            labeldict = None
            graph_viz = None

        return (all_class_names, all_node_states, 
                all_edge_ids, all_edge_types, mask_nodes, mask_edges), (graph_viz, labeldict)

def can_perform_action(action, o1, o2, agent_id, graph):
    num_args = len([None for ob in [o1, o2] if ob is not None])
    grabbed_objects = [edge['to_id'] for edge in graph['edges'] if edge['from_id'] == agent_id and edge['relation_type'] in ['HOLDS_RH', 'HOLD_LH']]
    if num_args != args_per_action(action):
        return False
    if 'put' in action:
        if o1 not in grabbed_objects:
            return False

    return True

def args_per_action(action):

    action_dict = {'turnleft': 0,
    'walkforward': 0,
    'turnright': 0,
    'walktowards': 0,
    'open': 1,
    'close': 1,
    'putback':2,
    'putin': 2,
    'grab': 1}
    return action_dict[action]

class GraphSpace(spaces.Space):
    def __init__(self):
        self.shape = None
        self.dtype = "graph"

        pass