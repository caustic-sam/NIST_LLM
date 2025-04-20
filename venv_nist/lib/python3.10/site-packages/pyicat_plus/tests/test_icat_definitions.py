from ..metadata.definitions import load_icat_fields
from ..metadata.definitions import IcatNodeId


def test_getitem():
    icat_fields = load_icat_fields()
    for field in icat_fields.iter_fields():
        assert icat_fields[field.node_id] == field


def test_node_id():
    assert IcatNodeId()[:] == IcatNodeId()
    assert IcatNodeId("a")[0] == "a"
    assert IcatNodeId(("a", "b", "c"))[:2] == IcatNodeId(("a", "b"))
    assert IcatNodeId(("a", "b", "c")).endswith(("b", "c"))


def test_find_groups_by_item_id():
    icat_fields = load_icat_fields()
    groups = list(icat_fields.iter_items_with_node_id_suffix("primary_slit"))
    assert len(groups) == 1
    assert str(groups[0].info.node_id) == "instrument.primary_slit"
