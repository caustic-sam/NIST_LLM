from treelib import Tree
from bliss.common.greenlet_utils.killmask import _main_greenlet, BlissGreenlet


def main_greenlet_tree() -> Tree:
    """Return main greenlet's children as a Tree"""
    return _dict_to_tree(Tree(), BlissGreenlet.child_dict(_main_greenlet))


def greenlet_as_tree(greenlet: BlissGreenlet) -> Tree:
    """Return a greenlet's children as a Tree"""
    assert isinstance(greenlet, BlissGreenlet)
    return _dict_to_tree(Tree(), greenlet.child_dict())


def _dict_to_tree(tree, dct, parent=None) -> Tree:
    """Recursively convert a greenlet child_dict to Tree"""
    glt = dct["greenlet"]
    node = tree.create_node(glt.name, glt, parent=parent)
    for child_dct in dct["children"]:
        if child_dct:
            _dict_to_tree(tree, child_dct, parent=node)
    return tree
