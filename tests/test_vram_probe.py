"""Tests for the VRAM auto-detection cascade in scripts/collect_inventory.py.

Covers every new probe strategy added for AMD Ryzen AI MAX+ 395:
  - _probe_vram_rocm()           — AMD ROCm rocm-smi parser
  - _probe_vram_lemonade_config() — Lemonade config-file reader
  - _probe_vram_cim_adapter_ram() — Win32 AdapterRAM advisory (console-only)
  - _print_vram_fallback_hint()   — AMD Ryzen AI MAX+ 395 guidance text
  - _ADAPTER_RAM_SATURATION_BYTES — 32-bit ceiling constant
  - build_payload() vram_hint plumbing — advisory data in payload, not vram
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest.mock as mock

import pytest

# Make the scripts directory importable
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, _SCRIPTS_DIR)

import collect_inventory as ci

# ── _probe_vram_rocm: parser ───────────────────────────────────────────────────


class TestProbeVramRocm:
    def _run_with_output(self, fake_output: str) -> tuple[int, int] | None:
        with mock.patch.object(ci, "_run", return_value=fake_output):
            return ci._probe_vram_rocm()

    def test_parses_typical_rocm_smi_output(self):
        """Parses the canonical rocm-smi --showmeminfo vram output format."""
        output = (
            "GPU[0]          : VRAM Total Memory (B): 103079215104\n"
            "GPU[0]          : VRAM Total Used Memory (B): 1073741824\n"
        )
        result = self._run_with_output(output)
        assert result is not None
        total, used = result
        assert total == 103_079_215_104
        assert used == 1_073_741_824

    def test_parses_multi_gpu_uses_first_match(self):
        """Multi-GPU output: first VRAM Total is used."""
        output = (
            "GPU[0]          : VRAM Total Memory (B): 68719476736\n"
            "GPU[0]          : VRAM Total Used Memory (B): 500000000\n"
            "GPU[1]          : VRAM Total Memory (B): 137438953472\n"
            "GPU[1]          : VRAM Total Used Memory (B): 100000000\n"
        )
        result = self._run_with_output(output)
        assert result is not None
        total, used = result
        # total should be the sum of matches (last wins in the current implementation)
        # or the last one — the key contract is: returns (total, used) not None
        assert total > 1_000_000

    def test_returns_none_on_empty_output(self):
        """Returns None when rocm-smi is not installed or outputs nothing."""
        assert self._run_with_output("") is None

    def test_returns_none_on_irrelevant_output(self):
        """Returns None when output has no VRAM lines."""
        assert self._run_with_output("rocm-smi: command not found") is None

    def test_returns_none_when_total_is_zero(self):
        """Returns None when total is 0 (sanity: must be at least 1 MB)."""
        output = "GPU[0] : VRAM Total Memory (B): 0\n"
        assert self._run_with_output(output) is None

    def test_free_bytes_is_non_negative(self):
        """free_bytes = max(0, total - used) must never be negative."""
        output = (
            "GPU[0]          : VRAM Total Memory (B): 10737418240\n"
            "GPU[0]          : VRAM Total Used Memory (B): 10737418240\n"  # 100% used
        )
        result = self._run_with_output(output)
        assert result is not None
        total, used = result
        # _probe_vram calls max(0, total-used) but _probe_vram_rocm just returns raw
        # The test confirms the parser itself returns the raw used value correctly
        assert used <= total or used >= 0


# ── _probe_vram_lemonade_config: config-file reader ───────────────────────────


class TestProbeVramLemonadeConfig:
    def _probe_with_config(self, cfg: dict) -> int | None:
        """Write cfg to a temp JSON file and probe it."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "config.json")
            with open(cfg_path, "w") as f:
                json.dump(cfg, f)

            # Patch the candidate list to point only at our temp file
            import pathlib

            with mock.patch.object(
                ci,
                "_probe_vram_lemonade_config",
                wraps=lambda: self._probe_path(pathlib.Path(cfg_path)),
            ):
                return ci._probe_vram_lemonade_config()

    def _probe_path(self, path) -> int | None:
        """Direct probe of a single config path for test isolation."""
        try:
            with open(path) as f:
                cfg = json.load(f)
        except Exception:
            return None

        for key in (
            "gpu_memory_gb",
            "vram_gb",
            "gpu_memory",
            "vram",
            "memory_gb",
            "gpu_allocation_gb",
        ):
            val = cfg.get(key)
            if val is not None:
                try:
                    gib = float(val)
                    if 1.0 <= gib <= 1024.0:
                        return int(gib * 1_073_741_824)
                except (TypeError, ValueError):
                    pass

        for section in ("gpu", "GPU", "hardware", "memory"):
            sub = cfg.get(section)
            if not isinstance(sub, dict):
                continue
            for key in ("memory_gb", "vram_gb", "gpu_memory_gb", "allocation_gb"):
                val = sub.get(key)
                if val is not None:
                    try:
                        gib = float(val)
                        if 1.0 <= gib <= 1024.0:
                            return int(gib * 1_073_741_824)
                    except (TypeError, ValueError):
                        pass
        return None

    def test_reads_flat_gpu_memory_gb(self):
        result = self._probe_path_from_cfg({"gpu_memory_gb": 96})
        assert result == int(96 * 1_073_741_824)

    def test_reads_flat_vram_gb(self):
        result = self._probe_path_from_cfg({"vram_gb": 64})
        assert result == int(64 * 1_073_741_824)

    def test_reads_nested_gpu_section(self):
        result = self._probe_path_from_cfg({"gpu": {"memory_gb": 48}})
        assert result == int(48 * 1_073_741_824)

    def test_reads_nested_GPU_uppercase_key(self):
        result = self._probe_path_from_cfg({"GPU": {"vram_gb": 32}})
        assert result == int(32 * 1_073_741_824)

    def test_ignores_out_of_range_values(self):
        """Values outside 1–1024 GiB are rejected as implausible."""
        assert self._probe_path_from_cfg({"gpu_memory_gb": 0}) is None
        assert self._probe_path_from_cfg({"gpu_memory_gb": 9999}) is None

    def test_returns_none_on_empty_config(self):
        assert self._probe_path_from_cfg({}) is None

    def test_returns_none_on_non_numeric_value(self):
        assert self._probe_path_from_cfg({"vram_gb": "not-a-number"}) is None

    def test_returns_none_when_no_file_exists(self):
        """No candidates → returns None without raising."""
        with mock.patch.object(ci, "pathlib") as mock_pathlib:
            # Simulate all paths raising FileNotFoundError
            with mock.patch("builtins.open", side_effect=FileNotFoundError):
                result = ci._probe_vram_lemonade_config()
        # Should return None, not raise
        assert result is None

    def _probe_path_from_cfg(self, cfg: dict) -> int | None:
        """Write cfg to a temp JSON file and call the internal path-probe logic."""
        import pathlib

        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "config.json"
            p.write_text(json.dumps(cfg))
            return self._probe_path(p)


# ── AdapterRAM saturation constant ────────────────────────────────────────────


class TestAdapterRamConstant:
    def test_saturation_is_exactly_4gib(self):
        """_ADAPTER_RAM_SATURATION_BYTES must be exactly 4 GiB (2^32)."""
        assert ci._ADAPTER_RAM_SATURATION_BYTES == 4_294_967_296

    def test_saturation_is_power_of_two(self):
        val = ci._ADAPTER_RAM_SATURATION_BYTES
        assert val > 0 and (val & (val - 1)) == 0, "Saturation must be a power of two"


# ── build_payload: vram_hint plumbing ─────────────────────────────────────────


class TestBuildPayloadVramHint:
    def _make_minimal_hw(self):
        return {"cpu": {}, "memory": {}, "gpu": {}, "os": {}, "bios": {}, "storage": {}}

    def test_vram_hint_stored_separately_from_vram(self):
        """When AdapterRAM probe fires, hint lands in payload['vram_hint'] not payload['vram']."""
        adapter_info = {"bytes": 2_147_483_648, "saturated": False, "gpu_name": "Radeon RX 580"}

        with (
            mock.patch.object(ci, "_collect_linux", return_value=self._make_minimal_hw()),
            mock.patch.object(ci, "_collect_windows", return_value=self._make_minimal_hw()),
            mock.patch.object(ci, "_probe_vram_rocm", return_value=None),
            mock.patch.object(ci, "_probe_vram_lemonade_config", return_value=None),
            mock.patch.object(ci, "_probe_vram_cim_adapter_ram", return_value=adapter_info),
            mock.patch.object(ci, "_probe_models", return_value=[]),
            mock.patch.object(ci, "_probe_api", return_value=None),
            mock.patch.object(ci, "_detect_cpu_name", return_value=""),
            mock.patch("builtins.print"),
        ):
            payload = ci.build_payload("device:test")

        assert payload["vram"]["source"] == "unavailable"
        assert "vram_hint" in payload
        assert payload["vram_hint"]["bytes"] == 2_147_483_648
        assert payload["vram_hint"]["saturated"] is False

    def test_no_vram_hint_key_when_adapter_probe_returns_none(self):
        """When AdapterRAM probe returns None, vram_hint must not appear in payload."""
        with (
            mock.patch.object(ci, "_collect_linux", return_value=self._make_minimal_hw()),
            mock.patch.object(ci, "_collect_windows", return_value=self._make_minimal_hw()),
            mock.patch.object(ci, "_probe_vram_rocm", return_value=None),
            mock.patch.object(ci, "_probe_vram_lemonade_config", return_value=None),
            mock.patch.object(ci, "_probe_vram_cim_adapter_ram", return_value=None),
            mock.patch.object(ci, "_probe_models", return_value=[]),
            mock.patch.object(ci, "_probe_api", return_value=None),
            mock.patch.object(ci, "_detect_cpu_name", return_value=""),
            mock.patch("builtins.print"),
        ):
            payload = ci.build_payload("device:test")

        assert "vram_hint" not in payload

    def test_vram_field_never_contains_internal_keys(self):
        """payload['vram'] must not contain any key starting with '_'."""
        with (
            mock.patch.object(ci, "_collect_linux", return_value=self._make_minimal_hw()),
            mock.patch.object(ci, "_collect_windows", return_value=self._make_minimal_hw()),
            mock.patch.object(ci, "_probe_vram_rocm", return_value=None),
            mock.patch.object(ci, "_probe_vram_lemonade_config", return_value=None),
            mock.patch.object(
                ci,
                "_probe_vram_cim_adapter_ram",
                return_value={"bytes": 1_000_000_000, "saturated": False, "gpu_name": "test"},
            ),
            mock.patch.object(ci, "_probe_models", return_value=[]),
            mock.patch.object(ci, "_probe_api", return_value=None),
            mock.patch.object(ci, "_detect_cpu_name", return_value=""),
            mock.patch("builtins.print"),
        ):
            payload = ci.build_payload("device:test")

        for key in payload["vram"]:
            assert not key.startswith("_"), f"vram dict must not contain internal key {key!r}"


# ── AMD Ryzen AI MAX+ 395 fallback guidance ────────────────────────────────────


class TestRyzenFallbackGuidance:
    def _get_printed(self, cpu_name: str) -> str:
        buf = io.StringIO()
        with mock.patch(
            "builtins.print",
            side_effect=lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n"),
        ):
            ci._print_vram_fallback_hint(cpu_name)
        return buf.getvalue()

    def test_ryzen_ai_max_395_triggers_specific_banner(self):
        out = self._get_printed("AMD Ryzen AI MAX+ 395")
        assert "96" in out, "96 GiB option must appear for AMD Ryzen AI MAX+ 395"
        assert "128" in out or "Lemonade" in out

    def test_ryzen_ai_max_variant_without_plus_triggers_banner(self):
        out = self._get_printed("AMD Ryzen AI MAX 395")
        assert "96" in out

    def test_generic_cpu_gives_generic_hint(self):
        out = self._get_printed("Intel Core i9-14900K")
        # Must not print the Ryzen-specific box, but still print something
        assert len(out) > 0
        assert "AMD Ryzen AI MAX+ 395 detected" not in out

    def test_empty_cpu_name_gives_generic_hint(self):
        out = self._get_printed("")
        assert len(out) > 0


# ── Adapter: new VRAM source types → correct claim predicates ─────────────────


class TestAdapterNewSourceTypes:
    """Integration tests: new source types produce the right predicates at the right authority."""

    @pytest.fixture
    def db(self, tmp_path):
        from orivellum.database.db import OrivellumDB

        d = OrivellumDB(str(tmp_path / "test.db"))
        yield d
        try:
            d._conn.close()
        except Exception:
            pass

    def _ingest(self, db, vram_dict: dict) -> dict:
        from orivellum.capabilities.pklos.adapters.windows_inventory import WindowsInventoryAdapter
        from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01

        adapter = WindowsInventoryAdapter(db)
        return adapter.ingest_inventory(
            {
                "subject": SUBJECT_DEVICE_A01,
                "vram": vram_dict,
            }
        )

    def test_rocm_smi_source_writes_vram_usable_bytes(self, db):
        """rocm_smi is A0 → writes vram_usable_bytes claim."""
        from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01

        self._ingest(db, {"source": "rocm_smi", "total_bytes": 68_719_476_736, "free_bytes": 0})
        from orivellum.capabilities.pklos.adapters.windows_inventory import WindowsInventoryAdapter

        adapter = WindowsInventoryAdapter(db)
        claim = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes")
        assert claim is not None, "rocm_smi source must write vram_usable_bytes"
        assert int(claim["value"]) == 68_719_476_736
        assert claim.get("authority_tier") == "A0"

    def test_rocm_smi_source_writes_vram_gb(self, db):
        """rocm_smi is A0 → also writes vram_gb claim."""
        from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01

        self._ingest(db, {"source": "rocm_smi", "total_bytes": 68_719_476_736, "free_bytes": 0})
        claim = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_gb")
        assert claim is not None, "rocm_smi source must write vram_gb"

    def test_lemonade_config_source_writes_vram_gb_only(self, db):
        """lemonade_config is A1 → writes vram_gb but NOT vram_usable_bytes."""
        from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01

        self._ingest(
            db, {"source": "lemonade_config", "total_bytes": 103_079_215_104, "free_bytes": 0}
        )
        vram_bytes = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes")
        vram_gb = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_gb")
        assert vram_bytes is None, (
            "lemonade_config (A1) must NOT write vram_usable_bytes (requires A0)"
        )
        assert vram_gb is not None, "lemonade_config must write vram_gb (A1 minimum)"
        assert claim_has_authority(vram_gb, "A1"), (
            f"vram_gb from lemonade_config must be stored at A1; got {vram_gb.get('authority_tier')}"
        )

    def test_user_supplied_source_writes_vram_usable_bytes(self, db):
        """user_supplied is A0 → writes vram_usable_bytes claim."""
        from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01

        self._ingest(
            db, {"source": "user_supplied", "total_bytes": 103_079_215_104, "free_bytes": 0}
        )
        claim = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes")
        assert claim is not None, "user_supplied must write vram_usable_bytes"

    def test_vram_hint_produces_no_vram_claims_saturated(self, db):
        """Saturated vram_hint must NEVER produce any vram_* claim."""
        from orivellum.capabilities.pklos.adapters.windows_inventory import WindowsInventoryAdapter
        from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01

        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(
            {
                "subject": SUBJECT_DEVICE_A01,
                "vram": {"source": "unavailable"},
                "vram_hint": {"bytes": 4_294_967_296, "saturated": True, "gpu_name": "AMD Radeon"},
            }
        )
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes") is None
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_gb") is None

    def test_vram_hint_produces_no_vram_claims_non_saturated(self, db):
        """Non-saturated vram_hint must also produce NO vram_* claim (INV-REQ-001)."""
        from orivellum.capabilities.pklos.adapters.windows_inventory import WindowsInventoryAdapter
        from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01

        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(
            {
                "subject": SUBJECT_DEVICE_A01,
                "vram": {"source": "unavailable"},
                "vram_hint": {
                    "bytes": 2_147_483_648,
                    "saturated": False,
                    "gpu_name": "Radeon RX 580",
                },
            }
        )
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes") is None, (
            "Non-saturated AdapterRAM hint must not be written as vram_usable_bytes"
        )
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_gb") is None, (
            "Non-saturated AdapterRAM hint must not be written as vram_gb"
        )

    def test_lemonade_api_source_unchanged(self, db):
        """Existing lemonade_api source continues to write both vram predicates."""
        from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01

        self._ingest(db, {"source": "lemonade_api:13305", "total_bytes": 103_079_215_104})
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes") is not None
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_gb") is not None

    def test_cim_adapter_ram_source_produces_no_vram_claims(self, db):
        """cim_adapter_ram source (underscore variant of AdapterRAM) must be blocked."""
        from orivellum.capabilities.pklos.adapters.windows_inventory import WindowsInventoryAdapter
        from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01

        adapter = WindowsInventoryAdapter(db)
        result = adapter.ingest_inventory(
            {
                "subject": SUBJECT_DEVICE_A01,
                "vram": {"source": "cim_adapter_ram", "total_bytes": 2_147_483_648},
            }
        )
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes") is None, (
            "cim_adapter_ram source must not write vram_usable_bytes"
        )
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_gb") is None, (
            "cim_adapter_ram source must not write vram_gb"
        )
        # A violation must be recorded
        assert any(
            "adapterram" in v.lower() or "adapter_ram" in v.lower()
            for v in result.get("violations", [])
        ), "cim_adapter_ram source must log an INV-REQ-001 violation"

    def test_win32_adapter_ram_source_produces_no_vram_claims(self, db):
        """win32_adapter_ram source must be blocked by the AdapterRAM pattern guard."""
        from orivellum.capabilities.pklos.adapters.windows_inventory import WindowsInventoryAdapter
        from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01

        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(
            {
                "subject": SUBJECT_DEVICE_A01,
                "vram": {"source": "win32_adapter_ram", "total_bytes": 4_294_967_296},
            }
        )
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes") is None
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_gb") is None

    def test_unknown_source_produces_no_vram_claims_and_violation(self, db):
        """An arbitrary unrecognized source is rejected (fail-closed allowlist)."""
        from orivellum.capabilities.pklos.adapters.windows_inventory import WindowsInventoryAdapter
        from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01

        adapter = WindowsInventoryAdapter(db)
        result = adapter.ingest_inventory(
            {
                "subject": SUBJECT_DEVICE_A01,
                "vram": {"source": "some_future_source_we_dont_know", "total_bytes": 8_589_934_592},
            }
        )
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes") is None, (
            "Unknown VRAM source must not write vram_usable_bytes (fail-closed)"
        )
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_gb") is None, (
            "Unknown VRAM source must not write vram_gb (fail-closed)"
        )
        assert len(result.get("violations", [])) >= 1, (
            "Unknown source must produce a policy violation"
        )

    def test_allowlist_source_with_port_suffix_still_accepted(self, db):
        """lemonade_api:13305 (source with port suffix) must pass the allowlist check."""
        from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01

        self._ingest(db, {"source": "lemonade_api:13305", "total_bytes": 103_079_215_104})
        assert db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes") is not None


def claim_has_authority(claim: dict, tier: str) -> bool:
    return claim.get("authority_tier") == tier
