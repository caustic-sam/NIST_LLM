# -*- coding: utf-8 -*-
#
# This file is part of the bliss project
#
# Copyright (c) Beamline Control Unit, ESRF
# Distributed under the GNU LGPLv3. See LICENSE for more info.import logging

import weakref
import logging
import networkx as nx

from functools import partial

from bliss import current_session
from bliss.common.proxy import ProxyWithoutCall

__all__ = ["Map", "format_node"]

logger = logging.getLogger(__name__)


def map_id(node):
    """
    Helper to get the proper node map id
    it will be the string itself if a string
    if the node is integer we assume that is already an id

    it will be the id if a different instance

    Needed to avoid errors caused by changing of string id
    """
    if isinstance(node, ProxyWithoutCall):
        node = node.__wrapped__
    if isinstance(node, (str, int)):
        return node
    elif isinstance(node, weakref.ProxyTypes):
        return id(node.__repr__.__self__)  # trick to get hard reference
    else:
        return id(node)


class Map:
    def __init__(self):
        self._init()

    def _init(self):
        self.G = nx.DiGraph()

        self.G.find_children = self.find_children
        self.G.find_predecessors = self.find_predecessors
        self.__waiting_queue = []
        self.__lock = False

        self.register("global")
        self.register("controllers", parents_list=["global"])
        self.register("comms", parents_list=["global"])
        self.register("counters", parents_list=["global"])
        self.register("axes", parents_list=["global"])

    def clear(self):
        self._init()

    def _create_node(self, instance):
        """
        Create a new node in the map for a given instance, if it does not exist already.

        Returns:
            tuple: (create_flag, node)
        """

        if isinstance(instance, weakref.ProxyTypes):
            instance = instance.__repr__.__self__  # trick to get the hard reference

        try:
            # a node already exist for this instance
            node = self.G.nodes[map_id(instance)]
            logger.debug("found existing node %s", node)
            return False, node

        except KeyError:
            # create a new node
            self.G.add_node(
                map_id(instance),
                instance=instance
                if isinstance(instance, str)
                else weakref.ref(
                    instance, partial(self._trash_node, id_=map_id(instance))
                ),
                version=0,
            )  # weakreference to the instance with callback on removal

            # add session key
            node = self.G.nodes[map_id(instance)]
            node["sessions"] = set()

            logger.debug("creating new node %s", node)

            return True, node

    def register(
        self, instance, parents_list=None, children_list=None, tag: str = None, **kwargs
    ):
        """
        Registers a devicename and instance inside a global device graph

        register(self)  # bareminimum
        register(self, children_list=[self.comm])  # with the communication layer
        register(self, parents_list=[self.controller])  # with parent controller
        register(self, tag=f"{host}:{port}")  # instance with proper name
        register(self, parents_list=['controllers','comms'])  # two parents

        If no parent is attached it will be 'controllers' and then eventually
        remapped if another instance will have as a child the other instance.

        There could be node parents in form of a string, system defined are:
            * 'controllers'
            * 'counters'
            * 'comms'

        Args:
            instance: instance of the object (usually self)
            parents_list: list of parent's instances
            children_list: list of children's instances
            tag: user tag to describe the instance in the more appropriate way
            kwargs: more key,value pairs attributes to be attached to the node


        ToDo:
            * Avoid recreation of nodes/edges if not necessary
        """
        session_name = current_session.name if current_session else None
        if self.__lock:
            node_info = {
                "instance": instance,
                "session": session_name,
                "parents_list": parents_list,
                "children_list": children_list,
                "tag": tag,
                "kwargs": kwargs,
            }

            self.__waiting_queue.append(("create", node_info))
        else:
            self._register(
                instance,
                session_name,
                parents_list=parents_list,
                children_list=children_list,
                tag=tag,
                **kwargs,
            )
            self.trigger_update()

    def _register(
        self,
        instance,
        session_name,
        parents_list=None,
        children_list=None,
        tag: str = None,
        **kwargs,
    ):

        if isinstance(instance, ProxyWithoutCall):
            instance = instance.__wrapped__

        # check if version is not part of keyword args
        if "version" in kwargs:
            raise ValueError("'version' is an internal keyword that cannot be used")

        if parents_list is None:
            parents_list = []
        if children_list is None:
            children_list = []

        if not isinstance(parents_list, (list, tuple, set)) or not isinstance(
            children_list, (list, tuple, set)
        ):
            raise TypeError(
                "parents_list and children_list should be of type list, tuple or set"
            )

        parents_dict = {map_id(parent): parent for parent in parents_list}
        children_dict = {map_id(child): child for child in children_list}

        # create or get this node
        create_flag, node = self._create_node(instance)

        # put new node without parents under 'controllers' node
        if (
            instance not in ("global", "controllers")
            and create_flag
            and not parents_dict
        ):
            parents_dict.update({"controllers": "controllers"})

        # add session name to 'sessions' node key
        node["sessions"].add(session_name)

        # handle tag key
        if tag:
            kwargs["tag"] = tag
        elif isinstance(instance, str):
            kwargs["tag"] = instance
        else:
            try:
                val = instance.name
            except AttributeError:
                pass
            else:
                if callable(val):  # tango device_proxy.name()
                    try:
                        kwargs["tag"] = val()
                    except Exception:
                        pass
                else:
                    kwargs["tag"] = val

        # add/update custom keys from kwargs
        for key, value in kwargs.items():
            curval = node.get(key)
            if curval is not None:
                if curval != value:
                    logger.warning(
                        "overwriting node['%s']: from %s to %s", key, curval, value
                    )
            node[key] = value

        # parents
        instance_id = map_id(instance)
        unexisting_parent_key = parents_dict.keys() - self.G
        edge_parent = parents_dict.keys() - unexisting_parent_key
        for inst_id in unexisting_parent_key:
            logger.debug("register parent:%s id:%s", parents_dict[inst_id], inst_id)
            self._register(
                parents_dict[inst_id], session_name, children_list=[instance]
            )  # register parents
        if edge_parent:
            logger.debug(
                "found parents edges with: %s",
                [parents_dict[parent_id] for parent_id in edge_parent],
            )
            self.G.add_edges_from(
                ((parent_id, instance_id) for parent_id in edge_parent)
            )

        # check if we have an edge with **controllers**
        controller_edge_removed = False
        controller_id = map_id("controllers")
        if not create_flag and (controller_id, instance_id) in self.G.edges:
            # check if one of the parent is not already a child of **controllers**
            possible_edge = set(
                [(controller_id, parent_id) for parent_id in parents_dict.keys()]
            )
            controller_children_edge = possible_edge.intersection(self.G.edges)
            if controller_children_edge:  # we will remove our edge with **controllers**
                logger.debug("remove edge with 'controllers' ")
                self.G.remove_edge(controller_id, instance_id)
                controller_edge_removed = True

        # children
        unexisting_children_key = children_dict.keys() - self.G
        edge_children = children_dict.keys() - unexisting_children_key
        for inst_id in unexisting_children_key:
            logger.debug("register child:%s id:%s", children_dict[inst_id], inst_id)
            self._register(
                children_dict[inst_id], session_name, parents_list=[instance]
            )  # register children
        if edge_children:
            logger.debug(
                "found edges with children: %s",
                [children_dict[child_id] for child_id in edge_children],
            )
            self.G.add_edges_from(
                ((instance_id, child_id) for child_id in edge_children)
            )
            for child_id in edge_children:
                child_node = self.G.nodes[child_id]
                logger.debug("reparent child %s under: %s", child_node, node)
                self._increment_version_number(child_node)

        # remap children removing the parent connection to controllers
        possible_edge = set([(controller_id, child_id) for child_id in edge_children])
        controller_children_edge = possible_edge.intersection(self.G.edges)
        if controller_children_edge:
            self.G.remove_edges_from(controller_children_edge)
            for _, child_id in controller_children_edge:
                child_node = self.G.nodes[child_id]
                logger.debug(
                    "remove edge with 'controllers' for: %s",
                    child_node,
                )

        if not create_flag and (
            unexisting_parent_key
            or edge_parent
            or unexisting_children_key
            or edge_children
            or controller_children_edge
            or controller_edge_removed
        ):
            # increment node version
            self._increment_version_number(node)

    def unregister(self, instance):
        return self._trash_node(id_=map_id(instance))

    def _increment_version_number(self, node):
        node["version"] += 1
        logger.debug("increment version number %s", node)
        try:
            for node_id in self.G[map_id(node["instance"])]:
                self._increment_version_number(self.G.nodes[node_id])
        except KeyError:
            pass

    def _trash_node(self, *args, id_=None):
        if id_ is None:
            return
        self.__waiting_queue.append(("delete", {"instance": id_}))
        if not self.__lock:
            self.trigger_update()

    def __len__(self):
        return len(self.G)

    def __getitem__(self, instance):
        if isinstance(instance, ProxyWithoutCall):
            instance = instance.__wrapped__

        return self.G.nodes[map_id(instance)]

    def __iter__(self):
        return iter(self.G)

    def instance_iter(self, tag, session_name=None):
        if session_name is None and current_session:
            session_name = current_session.name
        node_list = list(self.G[tag])
        for node_id in node_list:
            node = self.G.nodes.get(node_id)
            if node is not None:
                if session_name not in node["sessions"]:
                    continue
                try:
                    inst_ref = self.G.nodes.get(node_id)["instance"]
                except KeyError:
                    continue
                if isinstance(inst_ref, str):
                    yield from self.instance_iter(inst_ref)
                else:
                    inst = inst_ref()
                    if inst:
                        yield inst

    def protocol_iter(self, *protocols):
        node_list = list(self.G.nodes)
        for node_id in node_list:
            node = self.G.nodes.get(node_id)
            if node is not None:
                try:
                    inst_ref = self.G.nodes.get(node_id)["instance"]
                except KeyError:
                    continue
                if isinstance(inst_ref, str):
                    pass
                else:
                    inst = inst_ref()
                    if inst and isinstance(inst, protocols):
                        yield inst

    def walk_node(self, from_node):
        # fix issue #3269: prevent removal of nodes while traversing the map in "walk_node"
        self.__lock = True
        try:
            sub_map = nx.DiGraph()
            self.create_submap(sub_map, map_id(from_node))
            for node_id in sub_map.nodes():
                yield self.G.nodes[node_id]
        finally:
            self.__lock = False

    def trigger_update(self):
        """
        Triggers pending creation, deletion on the map
        """
        self.__lock = True  # no nested trigger update

        logger.debug("trigger_update: executing")
        try:
            while self.__waiting_queue:
                operation, node_info = self.__waiting_queue.pop()
                if operation == "delete":
                    self.delete(node_info["instance"])  # deleting node

                elif operation == "create":
                    instance = node_info["instance"]
                    parents_list = node_info["parents_list"]
                    children_list = node_info["children_list"]
                    tag = node_info["tag"]
                    kwargs = node_info["kwargs"]

                    self._register(
                        instance,
                        parents_list=parents_list,
                        children_list=children_list,
                        tag=tag,
                        **kwargs,
                    )
                else:
                    raise NotImplementedError

        finally:
            self.__lock = False  # we can trigger update again

        if self.__waiting_queue:
            # if in the meanwhile there are waiting nodes
            self.trigger_update()

    def find_predecessors(self, node):
        """
        Returns the predecessor of a node

        Args:
            node: instance or id(instance)
        Returns:
            list: id of predecessor nodes
        """
        id_ = map_id(node)
        return [n for n in self.G.predecessors(id_)]

    def find_children(self, node) -> list:
        """
        Args:
            node: instance or id(instance)
        Returns:
            list: id of first level child nodes
        """
        id_ = map_id(node)
        return [n for n in self.G.adj.get(id_)]

    def find_descendant(self, node) -> list:
        """
        Args:
            node: instance or id(instance)
        Returns:
            list: id of all descendant child nodes
        """
        if node not in self:
            return []
        sub_G = nx.DiGraph()
        self.create_submap(sub_G, node)
        return [n for n in sub_G]

    def find_tags(self, from_node, recursive=True) -> list:
        """
        Args:
            node: instance or id(instance)
        Returns:
            list: tags of all descendant child nodes
        """
        if recursive:
            return [node["tag"] for node in self.walk_node(from_node)]
        else:
            return [
                self.G.nodes[node_id]["tag"]
                for node_id in self.find_children(from_node)
            ]

    def shortest_path(self, node1, node2):
        """
        Args:
            node1: instance or id(instance)
            node2: instance or id(instance)

        Returns:
            list: path fron node1 to node2

        Raises:
            networkx.exception.NodeNotFound
            networkx.exception.NetworkXNoPath
        """
        id_1 = map_id(node1)
        id_2 = map_id(node2)
        return nx.shortest_path(self.G, id_1, id_2)

    def create_partial_map(self, sub_G, node):
        """
        Create a partial map containing all nodes that have some
        direct or indirect connection with the given one

        Args:
            sub_G: nx.DiGraph object that will be populated
            node: instance or id(instance)

        Returns:
            networkx.DiGraph
        """
        # UPSTREAM part of the map
        # getting all simple path from the root node "global"
        # to the given node
        logger.debug("In create_partial_map of %s map_id(%s)", node, map_id(node))
        paths = nx.all_simple_paths(self.G, "global", map_id(node))
        paths = list(paths)
        for path in map(nx.utils.pairwise, paths):
            for father, son in path:
                sub_G.add_node(
                    father, **self.G.nodes[father]
                )  # adds the node copying info
                sub_G.add_node(son, **self.G.nodes[son])  # adds the node copying info
                nx.add_path(sub_G, [father, son])

        # DOWNSTREAM part of the map
        # getting all nodes from the given node to the end of the map
        self.create_submap(sub_G, node)

    def create_submap(self, sub_G, node):
        """
        Create a submap starting from given node
        Args:
            sub_G: nx.DiGraph object that will be populated
            node: instance or id(instance) of the starting node

        Returns:
            networkx.DiGraph
        """
        id_ = map_id(node)
        sub_G.add_node(id_, **self.G.nodes[id_])  # adds the node copying info
        for n in self.G.adj.get(id_):
            if n not in sub_G.neighbors(id_):
                nx.add_path(sub_G, [id_, n])
                sub_G.nodes[id_]
                self.create_submap(sub_G, n)

    def delete(self, id_):
        """
        Removes the node from graph

        Args:
            id_: id of node to be deleted

        Returns:
            True: The node was removed
            False: The node was not in the graph
        """
        logger.debug("Calling mapping.delete for %s", id_)
        try:
            self.G.remove_node(id_)
        except nx.NetworkXError:
            return False
        logger.debug("mapping.delete: Removing node id:%s", id_)
        return True

    def draw(
        self,
        ref_node=None,
        map_style="planar",
        font_size=8,
        format_string="tag->name->class->id",
        **kwargs,
    ) -> None:
        """
        draw the map nodes with matplotlib using the node tag as plot labels.

        Args:
            ref_node: If given a partial map will be drawn that includes the given node and his area of interest
            map_style: the style/layout of the map in [circular, kawai, planar, random, shell, spectral, spring]


        arrows : bool or None, optional (default=None)
            If 'None', directed graphs draw arrowheads with
            '~matplotlib.patches.FancyArrowPatch', while undirected graphs draw edges
            via '~matplotlib.collections.LineCollection' for speed.
            If 'True', draw arrowheads with FancyArrowPatches (bendable and stylish).
            If 'False', draw edges using LineCollection (linear and fast).
            For directed graphs, if True draw arrowheads.
            Note: Arrows will be the same color as edges.

        arrowstyle : str (default='-|>' for directed graphs)
            For directed graphs, choose the style of the arrowsheads.
            For undirected graphs default to '-'

            See 'matplotlib.patches.ArrowStyle' for more options.

        arrowsize : int or list (default=10)
            For directed graphs, choose the size of the arrow head's length and
            width. A list of values can be passed in to assign a different size for arrow head's length and width.
            See 'matplotlib.patches.FancyArrowPatch' for attribute 'mutation_scale'
            for more info.

        with_labels :  bool (default=True)
            Set to True to draw labels on the nodes.

        ax : Matplotlib Axes object, optional
            Draw the graph in the specified Matplotlib axes.

        nodelist : list (default=list(G))
            Draw only specified nodes

        edgelist : list (default=list(G.edges()))
            Draw only specified edges

        node_size : scalar or array (default=300)
            Size of nodes.  If an array is specified it must be the
            same length as nodelist.

        node_color : color or array of colors (default='#1f78b4')
            Node color. Can be a single color or a sequence of colors with the same
            length as nodelist. Color can be string or rgb (or rgba) tuple of
            floats from 0-1. If numeric values are specified they will be
            mapped to colors using the cmap and vmin,vmax parameters. See
            matplotlib.scatter for more details.

        node_shape :  string (default='o')
            The shape of the node.  Specification is as matplotlib.scatter
            marker, one of 'so^>v<dph8'.

        alpha : float or None (default=None)
            The node and edge transparency

        cmap : Matplotlib colormap, optional
            Colormap for mapping intensities of nodes

        vmin,vmax : float, optional
            Minimum and maximum for node colormap scaling

        linewidths : scalar or sequence (default=1.0)
            Line width of symbol border

        width : float or array of floats (default=1.0)
            Line width of edges

        edge_color : color or array of colors (default='k')
            Edge color. Can be a single color or a sequence of colors with the same
            length as edgelist. Color can be string or rgb (or rgba) tuple of
            floats from 0-1. If numeric values are specified they will be
            mapped to colors using the edge_cmap and edge_vmin,edge_vmax parameters.

        edge_cmap : Matplotlib colormap, optional
            Colormap for mapping intensities of edges

        edge_vmin,edge_vmax : floats, optional
            Minimum and maximum for edge colormap scaling

        style : string (default=solid line)
            Edge line style e.g.: '-', '--', '-.', ':'
            or words like 'solid' or 'dashed'.
            (See 'matplotlib.patches.FancyArrowPatch': 'linestyle')

        labels : dictionary (default=None)
            Node labels in a dictionary of text labels keyed by node

        font_size : int (default=12 for nodes, 10 for edges)
            Font size for text labels

        font_color : string (default='k' black)
            Font color string

        font_weight : string (default='normal')
            Font weight

        font_family : string (default='sans-serif')
            Font family

        label : string, optional
            Label for graph legend
        """

        available_map_styles = {
            "circular": nx.circular_layout,
            "kawai": nx.kamada_kawai_layout,
            "random": nx.random_layout,
            "spectral": nx.spectral_layout,
            "spring": nx.spring_layout,
            "shell": nx.shell_layout,
            "planar": nx.planar_layout,
        }

        if map_style not in available_map_styles:
            raise ValueError(f"map_style should be in {available_map_styles}")

        if ref_node is not None:
            G = nx.DiGraph()
            self.create_submap(G, map_id(ref_node))
        else:
            G = self.G

        labels = {node: format_node(G, node, format_string) for node in G}

        try:
            pos = available_map_styles[map_style](G)
        except nx.NetworkXException as e:
            if "is not planar" in e.args[0]:
                pos = available_map_styles["shell"](G)

        import matplotlib.pyplot as plt

        nx.draw_networkx(
            G,
            pos=pos,
            ax=None,
            with_labels=True,
            labels=labels,
            font_size=font_size,
            **kwargs,
        )
        plt.axis("off")
        plt.show()

    def format_node(self, node, format_string):
        return format_node(self.G, node, format_string)


def format_node(graph, node, format_string="tag->name->class->id"):
    """
    It inspects the node attributes to create a proper representation

    It recognizes the following operators:
       * inst.
       * -> : apply a hierarchy, if the first on left is found it stops,
              otherwise continues searching for an attribute
       * + : links two attributes in one

    Typical attribute names are:
       * id: id of instance
       * tag: defined argument during instantiation
       * class: class of the instance
       * inst: representation of instance
       * inst.name: attribute "name" of the instance (if present)
       * user defined: as long as they are defined inside the node's
                       dictionary using register or later modifications

    Args:
       graph: DiGraph instance
       node: id of the node
       format_string: formatting string

    Returns:
       str: representation of the node according to the format string

    """
    G = graph
    n = node
    format_arguments = format_string.split("->")
    value = ""  # clears the dict_key
    reference = G.nodes[n].get("instance")
    inst = reference if isinstance(reference, str) else reference()
    if inst is None:
        raise RuntimeError(
            "Trying to get string representation of garbage collected node instance"
        )

    for format_arg in format_arguments:
        # known arguments
        all_args = []
        for arg in format_arg.split("+"):
            if arg == "id":
                all_args.append(str(n))
            elif arg == "class":
                if not isinstance(inst, str):
                    all_args.append(inst.__class__.__name__)
            elif arg.startswith("inst"):
                attr_name = arg[5:]  # separates inst. from the rest
                if len(attr_name) == 0:  # requested only instance
                    all_args.append(str(inst))
                if hasattr(inst, attr_name):
                    # if finds the attr assigns to dict_key
                    attr = getattr(inst, attr_name)
                    all_args.append(str(attr))
            else:
                val = G.nodes[n].get(arg)
                if val:
                    # if finds the value assigns to dict_key
                    all_args.append(str(val))
        if len(all_args):
            value = " ".join(all_args)
            break
    return value
