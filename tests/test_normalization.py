"""Tests for normalization components."""

import pytest
from unittest.mock import AsyncMock

from app.normalization.cache import NormalizationCache
from app.normalization.category_normalizer import CategoryNormalizer
from app.normalization.material_normalizer import MaterialNormalizer
from app.normalization.step_normalizer import StepNormalizer
from app.normalization.synonym_map import SYNONYM_MAP, resolve_static


class TestSynonymMap:
    def test_direct_lookup(self):
        assert resolve_static("cotton") == "fibre, cotton"

    def test_polyester(self):
        assert resolve_static("polyester") == "fibre, polyester"

    def test_nylon(self):
        assert resolve_static("nylon") == "nylon 6"

    def test_prefix_stripping(self):
        assert resolve_static("canopy: fibre, cotton") == "fibre, cotton"

    def test_unknown_returns_none(self):
        assert resolve_static("unobtanium") is None

    def test_empty_returns_none(self):
        assert resolve_static("") is None

    def test_synonym_map_size(self):
        assert len(SYNONYM_MAP) > 200


class TestNormalizationCache:
    @pytest.mark.asyncio
    async def test_get_miss(self, cache):
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_and_get(self, cache):
        await cache.set("key1", "value1")
        result = await cache.get("key1")
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_hit_rate(self, cache):
        await cache.set("key1", "value1")
        await cache.get("key1")  # hit
        await cache.get("key2")  # miss
        assert cache.hit_rate == pytest.approx(0.5)

    def test_stats(self, cache):
        stats = cache.stats()
        assert "size" in stats
        assert "hit_rate" in stats


class TestMaterialNormalizer:
    @pytest.mark.asyncio
    async def test_static_resolution(self, mock_nim_client, cache):
        norm = MaterialNormalizer(mock_nim_client, cache)
        result = await norm.normalize("cotton")
        assert result == "fibre, cotton"
        mock_nim_client.complete_with_choices.assert_not_called()

    @pytest.mark.asyncio
    async def test_nim_fallback(self, mock_nim_client, cache):
        mock_nim_client.complete_with_choices.return_value = "fibre, cotton"
        norm = MaterialNormalizer(mock_nim_client, cache)
        result = await norm.normalize("organic bamboo cotton blend")
        assert result == "fibre, cotton"
        mock_nim_client.complete_with_choices.assert_called_once()

    @pytest.mark.asyncio
    async def test_caching(self, mock_nim_client, cache):
        mock_nim_client.complete_with_choices.return_value = "fibre, cotton"
        norm = MaterialNormalizer(mock_nim_client, cache)
        await norm.normalize("strange material xyz")
        await norm.normalize("strange material xyz")
        # NIM should only be called once due to caching
        assert mock_nim_client.complete_with_choices.call_count <= 1


class TestMaterialNormalizerBulk:
    @pytest.mark.asyncio
    async def test_bulk_dedup_and_mapping(self, mock_nim_client, cache):
        """Bulk normalization deduplicates unknowns and returns correct mapping."""
        mock_nim_client.complete_bulk.return_value = [
            "fibre, polyester",
            "cellulose fibre",
        ]
        norm = MaterialNormalizer(mock_nim_client, cache)
        result = await norm.normalize_bulk([
            "cotton",           # static hit
            "zorplex fiber",    # unknown
            "zorplex fiber",    # duplicate unknown
            "mycelium leather", # unknown
        ])
        assert result["cotton"] == "fibre, cotton"
        assert result["zorplex fiber"] == "fibre, polyester"
        assert result["mycelium leather"] == "cellulose fibre"
        # Only one bulk call for the 2 unique unknowns
        mock_nim_client.complete_bulk.assert_called_once()
        call_args = mock_nim_client.complete_bulk.call_args
        items = call_args[0][1]  # second positional arg
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_bulk_all_static(self, mock_nim_client, cache):
        """No NIM call when all materials resolve statically."""
        norm = MaterialNormalizer(mock_nim_client, cache)
        result = await norm.normalize_bulk(["cotton", "polyester", "nylon"])
        assert result["cotton"] == "fibre, cotton"
        assert result["polyester"] == "fibre, polyester"
        assert result["nylon"] == "nylon 6"
        mock_nim_client.complete_bulk.assert_not_called()

    @pytest.mark.asyncio
    async def test_bulk_fallback_on_failure(self, mock_nim_client, cache):
        """Falls back to per-item normalization when bulk call fails."""
        mock_nim_client.complete_bulk.side_effect = Exception("NIM down")
        mock_nim_client.complete_with_choices.return_value = "fibre, viscose"
        norm = MaterialNormalizer(mock_nim_client, cache)
        result = await norm.normalize_bulk(["zorplex fiber"])
        assert result["zorplex fiber"] == "fibre, viscose"
        mock_nim_client.complete_with_choices.assert_called_once()


class TestStepNormalizerBulk:
    @pytest.mark.asyncio
    async def test_bulk_dedup_and_mapping(self, mock_nim_client, cache):
        """Bulk step normalization deduplicates and maps correctly."""
        mock_nim_client.complete_bulk.return_value = [
            "printing",
            "pressing",
        ]
        norm = StepNormalizer(mock_nim_client, cache)
        result = await norm.normalize_bulk([
            "cutting",         # static hit
            "laser etching",   # unknown
            "heat seal",       # unknown
        ])
        assert result["cutting"] == "cutting"
        assert result["laser etching"] == "printing"
        assert result["heat seal"] == "pressing"
        mock_nim_client.complete_bulk.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_all_static(self, mock_nim_client, cache):
        """No NIM call when all steps resolve statically."""
        norm = StepNormalizer(mock_nim_client, cache)
        result = await norm.normalize_bulk([
            "cutting", "sewing", "stitching",
        ])
        assert result["cutting"] == "cutting"
        assert result["sewing"] == "sewing"
        assert result["stitching"] == "sewing"
        mock_nim_client.complete_bulk.assert_not_called()


class TestCategoryNormalizerBulk:
    @pytest.mark.asyncio
    async def test_bulk_dedup_and_mapping(self, mock_nim_client, cache):
        """Bulk category normalization maps unknown roots."""
        mock_nim_client.complete_bulk.return_value = [
            "Clothing",
            "Home Textiles",
        ]
        norm = CategoryNormalizer(mock_nim_client, cache)
        result = await norm.normalize_bulk([
            "Apparel",     # unknown
            "Home Decor",  # unknown
        ])
        assert result["Apparel"] == "Clothing"
        assert result["Home Decor"] == "Home Textiles"
        mock_nim_client.complete_bulk.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_all_known(self, mock_nim_client, cache):
        """No NIM call when all roots are in static map."""
        norm = CategoryNormalizer(mock_nim_client, cache)
        result = await norm.normalize_bulk(["Clothing", "Footwear"])
        assert result["Clothing"] == "Clothing"
        assert result["Footwear"] == "Footwear"
        mock_nim_client.complete_bulk.assert_not_called()


class TestGuidedChoicePayload:
    @pytest.mark.asyncio
    async def test_material_uses_guided_choice(self, mock_nim_client, cache):
        """Material normalizer passes guided_choice to NIM."""
        mock_nim_client.complete_with_choices.return_value = "fibre, cotton"
        norm = MaterialNormalizer(mock_nim_client, cache)
        await norm.normalize("unknown fiber xyz")
        mock_nim_client.complete_with_choices.assert_called_once()
        call_args = mock_nim_client.complete_with_choices.call_args
        choices = call_args[0][2]  # third positional arg
        assert isinstance(choices, list)
        assert len(choices) > 10
        assert "fibre, cotton" in choices

    @pytest.mark.asyncio
    async def test_step_uses_guided_choice(self, mock_nim_client, cache):
        """Step normalizer passes guided_choice to NIM."""
        mock_nim_client.complete_with_choices.return_value = "pressing"
        norm = StepNormalizer(mock_nim_client, cache)
        await norm._normalize_single("laser bonding")
        mock_nim_client.complete_with_choices.assert_called_once()
        call_args = mock_nim_client.complete_with_choices.call_args
        choices = call_args[0][2]
        assert "unknown" in choices
        assert "pressing" in choices

    @pytest.mark.asyncio
    async def test_category_uses_guided_choice(self, mock_nim_client, cache):
        """Category normalizer passes guided_choice to NIM."""
        mock_nim_client.complete_with_choices.return_value = "Clothing"
        norm = CategoryNormalizer(mock_nim_client, cache)
        await norm.normalize(["Garments", "Coats"])
        mock_nim_client.complete_with_choices.assert_called_once()
        call_args = mock_nim_client.complete_with_choices.call_args
        choices = call_args[0][2]
        assert "Clothing" in choices
        assert "Footwear" in choices


class TestCategoryNormalizer:
    @pytest.mark.asyncio
    async def test_known_root(self, mock_nim_client, cache):
        norm = CategoryNormalizer(mock_nim_client, cache)
        cat, subcat = await norm.normalize(["Clothing", "Coats"])
        assert cat == "Clothing"
        assert subcat == "Coats"

    @pytest.mark.asyncio
    async def test_single_level(self, mock_nim_client, cache):
        norm = CategoryNormalizer(mock_nim_client, cache)
        cat, subcat = await norm.normalize(["Footwear"])
        assert cat == "Footwear"
        assert subcat == "Footwear"

    @pytest.mark.asyncio
    async def test_empty_path(self, mock_nim_client, cache):
        norm = CategoryNormalizer(mock_nim_client, cache)
        cat, subcat = await norm.normalize([])
        assert cat == "Unknown"
        assert subcat == "Unknown"


class TestStepNormalizer:
    @pytest.mark.asyncio
    async def test_known_steps(self, mock_nim_client, cache):
        norm = StepNormalizer(mock_nim_client, cache)
        result = await norm.normalize(["cutting", "sewing", "dyeing"])
        assert "cutting" in result
        assert "sewing" in result
        assert "dyeing" in result

    @pytest.mark.asyncio
    async def test_synonym_step(self, mock_nim_client, cache):
        norm = StepNormalizer(mock_nim_client, cache)
        result = await norm.normalize(["stitching"])
        assert "sewing" in result
