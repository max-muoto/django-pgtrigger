from __future__ import annotations

import collections
from typing import Callable, Dict, List, Set, Tuple, Type, TypeVar

from django.db import models
from django.db.models import base as models_base

from pgtrigger import features
from pgtrigger.core import Trigger

_B = TypeVar("_B", bound=models_base.ModelBase)


# All registered triggers for each model


class _Registry(collections.UserDict[str, Tuple[Type[models.Model], Trigger]]):
    @property
    def pg_function_names(self) -> Set[str]:
        """
        The postgres function names of all registered triggers
        """
        return {trigger.get_pgid(model) for model, trigger in self.values()}

    @property
    def by_db_table(self) -> Dict[Tuple[str, str], Trigger]:
        """
        Return the registry keys by db_table, name
        """
        return {(model._meta.db_table, trigger.name): trigger for model, trigger in self.values()}

    def __getitem__(self, key: str) -> Tuple[Type[models.Model], Trigger]:
        assert isinstance(key, str)
        if len(key.split(":")) == 1:
            raise ValueError(
                'Trigger URI must be in the format of "app_label.model_name:trigger_name"'
            )
        elif key not in _registry:
            raise KeyError(f'URI "{key}" not found in pgtrigger registry')

        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Tuple[Type[models.Model], Trigger]) -> None:
        assert isinstance(key, str)
        model, trigger = value
        assert f"{model._meta.label}:{trigger.name}" == key

        found_trigger = self.by_db_table.get((model._meta.db_table, trigger.name))

        if not found_trigger or found_trigger != trigger:
            if found_trigger:
                raise KeyError(
                    f'Trigger name "{trigger.name}" already'
                    f' used for model "{model._meta.label}"'
                    f' table "{model._meta.db_table}".'
                )

            if trigger.get_pgid(model) in self.pg_function_names:
                raise KeyError(
                    f'Trigger "{trigger.name}" on model "{model._meta.label}"'
                    " has Postgres function name that's already in use."
                    " Use a different name for the trigger."
                )

        # Add the trigger to Meta.triggers.
        # Note, pgtrigger's App.ready() method auto-registers any
        # triggers in Meta already, meaning the trigger may already exist. If so, ignore it
        if features.migrations():  # pragma: no branch
            if trigger not in getattr(model._meta, "triggers", []):
                model._meta.triggers = list(getattr(model._meta, "triggers", [])) + [trigger]  # type: ignore

            if trigger not in model._meta.original_attrs.get("triggers", []):
                model._meta.original_attrs["triggers"] = list(
                    model._meta.original_attrs.get("triggers", [])
                ) + [trigger]

        return super().__setitem__(key, value)

    def __delitem__(self, key: str) -> None:
        model, trigger = self[key]

        super().__delitem__(key)

        # If we support migration integration, remove from Meta triggers
        if features.migrations():  # pragma: no branch
            model._meta.triggers.remove(trigger)  # type: ignore
            # If model._meta.triggers and the original_attrs triggers are the same,
            # we don't need to remove it from the original_attrs
            if trigger in model._meta.original_attrs["triggers"]:  # pragma: no branch
                model._meta.original_attrs["triggers"].remove(trigger)


_registry = _Registry()


def set(uri: str, *, model: Type[models.Model], trigger: Trigger) -> None:
    """Set a trigger in the registry

    Args:
        uri: The trigger URI
        model: The trigger model
        trigger: The trigger object
    """
    _registry[uri] = (model, trigger)


def delete(uri: str) -> None:
    """Delete a trigger from the registry.

    Args:
        uri: The trigger URI
    """
    del _registry[uri]


def registered(*uris: str) -> List[Tuple[Type[models.Model], Trigger]]:
    """
    Get registered trigger objects.

    Args:
        *uris: URIs of triggers to get. If none are provided,
            all triggers are returned. URIs are in the format of
            `{app_label}.{model_name}:{trigger_name}`.

    Returns:
        Matching trigger objects.
    """
    return [_registry[uri] for uri in uris or _registry.keys()]


def register(*triggers: Trigger) -> Callable[[_B], _B]:
    """
    Register the given triggers with wrapped Model class.

    Args:
        *triggers: Trigger classes to register.

    Example:
        Register by decorating a model:

            @pgtrigger.register(
                pgtrigger.Protect(
                    name="append_only",
                    operation=(pgtrigger.Update | pgtrigger.Delete)
                )
            )
            class MyModel(models.Model):
                pass

    Example:
        Register by calling functionally:

            pgtrigger.register(trigger_object)(MyModel)
    """

    def _model_wrapper(model_class: _B) -> _B:
        for trigger in triggers:
            trigger.register(model_class)

        return model_class

    return _model_wrapper
