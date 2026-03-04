"""Model loader: loads all three ESPResso model artifacts at startup."""

import logging
from pathlib import Path
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)


class ModelLoader:
    """Singleton-style loader for ESPResso model artifacts."""

    def __init__(self, settings: Settings):
        self._paths = {
            "A": Path(settings.MODEL_A_PATH),
            "B": Path(settings.MODEL_B_PATH),
            "C": Path(settings.MODEL_C_PATH),
        }
        self._models: dict[str, Any] = {}
        self._loaded = False

    def load_all(self) -> None:
        """Load all model artifacts from disk.

        Uses vendored classes from espresso_models for deserialization.
        """
        from espresso_models.model_a.model import CarbonFootprintModel
        from espresso_models.model_b.model import CarbonFootprintModelB
        from espresso_models.model_c.model import CarbonFootprintModelC

        loaders = {
            "A": CarbonFootprintModel.load,
            "B": CarbonFootprintModelB.load,
            "C": CarbonFootprintModelC.load,
        }

        for name, path in self._paths.items():
            if path.exists():
                logger.info("Loading Model %s from %s", name, path)
                self._models[name] = loaders[name](path)
                logger.info("Model %s loaded successfully", name)
            else:
                logger.warning(
                    "Model %s artifact not found at %s", name, path
                )

        self._loaded = True

    def get(self, name: str) -> Any:
        """Get a loaded model by name (A, B, or C).

        Raises:
            KeyError: If model is not loaded.
        """
        if name not in self._models:
            raise KeyError(
                f"Model {name} not loaded. "
                f"Available: {list(self._models.keys())}"
            )
        return self._models[name]

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def status(self) -> dict[str, bool]:
        """Return load status for each model."""
        return {
            name: name in self._models
            for name in ("A", "B", "C")
        }
