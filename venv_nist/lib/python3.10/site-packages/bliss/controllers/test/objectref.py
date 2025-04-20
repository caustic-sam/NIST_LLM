from bliss.config.beacon_object import BeaconObject


class ObjectRef(BeaconObject):
    """
    Define a BLISS object referring another BLISS object.

    .. code-block::

        - name: objectref1
          plugin: bliss
          package: bliss.controllers.test.objectref
          class: ObjectRef
          ref: $roby

        - name: objectref2
          plugin: bliss
          package: bliss.controllers.test.objectref
          class: ObjectRef
          ref: !!null

    This is mostly used for unit tests.
    """

    def __init__(self, name, config):
        BeaconObject.__init__(self, config=config, name=name)

    ref = BeaconObject.config_obj_property_setting(name="ref")

    def __info__(self):
        if self.ref is None:
            ref = "None"
        else:
            ref = f"${self.ref.name}"

        info_str = f"{self.name} references {ref}\n"
        return info_str
