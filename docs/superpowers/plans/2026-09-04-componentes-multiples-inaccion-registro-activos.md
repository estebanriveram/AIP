# Componentes múltiples de inacción, Registro de Activos y horizonte único — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `ProjectInput.risk: Optional[RiskInput]` with `risk_components: List[RiskInput]` (aggregated, summed), replace the per-asset-type horizon lookup with a single analyst-declared `analysis_horizon_years` per project, and add a minimal Asset Registry (`AssetRegistryStore`) — per the approved spec at `docs/superpowers/specs/2026-09-04-componentes-multiples-inaccion-registro-activos-design.md`.

**Architecture:** This is a clean, non-backward-compatible field replacement (explicit spec decision, §9) inside the existing single-file engine `epm_core.py`. Because there is no compatibility shim, the dataclass change breaks every call site simultaneously — Tasks 1-13 must land together as one atomic unit before the test suite is runnable again (this is a deliberate, documented deviation from strict per-task TDD greenness; see "A note on Tasks 1-13" below). Tasks 14+ (Asset Registry, wizard UI) are additive and independently TDD-able.

**Tech Stack:** Python (dataclasses, pulp for MILP), Streamlit (`app_epm_aip_full_enterprise.py`), unittest (`test_suite_epm_full.py`).

---

## A note on Tasks 1-13 (read this before starting)

`ProjectInput.risk` is read in ~15 distinct places across `epm_core.py` (the main evaluation path, the year-scanning optimal-timing engine, a sensitivity-analysis utility, two MILP constraint builders, a CLI wizard, and a bulk portfolio importer) plus ~15 fixture/call sites in `test_suite_epm_full.py` and 2 in `app_epm_aip_full_enterprise.py`. The spec's own §4/§5 only enumerated the 4-6 sites inside the main `evaluate()` path — deeper research for this plan (documented inline in each task) found the rest, including **one pre-existing, currently-dormant bug** (Task 9) and **one call site the spec's design, if implemented literally, would have broken invisibly** (Task 5's "aggregate compatibility keys" design, needed to keep the mutation-tested `S22_RamaWeibull` guardian class passing).

Do not attempt to keep the test suite green between Tasks 1-13 — it cannot be, because the dataclass field genuinely stops existing partway through. Make all edits in Tasks 1-13, in order, then run the full suite once at the end of Task 13. Commit once, at the end of Task 13, not per-task.

---

## File Structure

- Modify `epm_core.py`: `RiskInput`, `ProjectInput`, `AssetProfile` dataclasses; `ValueEngine._evaluate_inner`, `.evaluate_marginal`, `._calc_timing`, `._optimal_timing_inner`/`._optimal_timing_inner`'s caller; `SensitivityAnalyst._apply_proj_overrides`; `PortfolioOptimizer.optimize` (R8/R6 constraints); `ProjectWizard._capture_risk`/`.run`; `PortfolioLoader._build_projects`; new `AssetRegistryStore` class (placed after `SnapshotStore`, ~line 10132).
- Modify `app_epm_aip_full_enterprise.py`: Paso 1 (horizon field + conditional Familia de Activo), Paso 3 "Do Nothing" → "Componentes de Inacción", per-alternative residual declaration, results display.
- Modify `test_suite_epm_full.py`: `_make_project` factory, ~12 individual test fixtures (enumerated below with exact fix per site), 2 new test files/classes for `AssetRegistryStore`.
- Modify `REGISTRO_ASUNTOS_ABIERTOS.md`: new D-14 entry (Task 19).

No changes to `config.json` are needed — no new config keys are introduced by this feature.

---

## Task 1: `RiskInput` — nuevos campos (`label`, `asset_type`, `source_asset_id`)

Additive only, safe on its own, does not touch `ProjectInput`. Can be committed independently if you want a checkpoint before the breaking change.

**Files:**
- Modify: `epm_core.py:2061-2063` (end of `RiskInput`, right after `residual_annual_COP`)
- Test: `test_suite_epm_full.py` (new test in `S1_ValueEngine` or a new small class — either is fine, this is a pure dataclass-defaults check)

- [ ] **Step 1: Write the failing test**

Add to `test_suite_epm_full.py`, right after the `_make_risk` helper (around line 154):

```python
class S33_RiskInputCamposComponente(unittest.TestCase):
    """RiskInput gana label/asset_type/source_asset_id (D-14) — defaults
    vacíos, no rompen ninguna construcción existente."""

    def test_nuevos_campos_tienen_default_vacio(self):
        r = RiskInput(baseline_consequence_level=3, baseline_probability_level=3)
        self.assertEqual(r.label, "")
        self.assertEqual(r.asset_type, "")
        self.assertEqual(r.source_asset_id, "")

    def test_nuevos_campos_se_pueden_declarar(self):
        r = RiskInput(
            baseline_consequence_level=3, baseline_probability_level=3,
            label="Tubería sector norte", asset_type="tuberia_ac",
            source_asset_id="AyA-TUB-001",
        )
        self.assertEqual(r.label, "Tubería sector norte")
        self.assertEqual(r.asset_type, "tuberia_ac")
        self.assertEqual(r.source_asset_id, "AyA-TUB-001")
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
python -m pytest test_suite_epm_full.py -k S33_RiskInputCamposComponente -v
```
Expected: FAIL — `TypeError: RiskInput.__init__() got an unexpected keyword argument 'label'` (for the second test); the first test also fails with `AttributeError: 'RiskInput' object has no attribute 'label'`.

- [ ] **Step 3: Add the fields**

In `epm_core.py`, at the end of the `RiskInput` dataclass (currently ends at line 2062 with `residual_annual_COP: float = 0.0`), add:

```python
    baseline_annual_COP: float = 0.0   # Costo anual de NO intervenir, en pesos
    residual_annual_COP: float = 0.0   # Costo anual que queda tras intervenir

    # ── D-14: identidad del componente, para desglose multi-componente ──────
    label: str = ""
    """Nombre del componente para el desglose (ej. 'Tubería sector norte',
    'Costo directo expansión') — sin esto, un desglose de varios componentes
    no significa nada para quien lo lee."""

    asset_type: str = ""
    """Tipo de activo de ESTE componente — cada componente puede ser un tipo
    de activo distinto (o ninguno, si es costo directo). Único consumidor:
    ValueEngine._get_risk_growth_rate() por componente (ver Task 5)."""

    source_asset_id: str = ""
    """Referencia al activo del Registro (Task 14) del que se copiaron los
    valores de este componente, si vino de ahí. NO es un enlace vivo — es
    trazabilidad de origen. Editar el activo en el Registro después NO
    cambia este componente ni ningún resultado ya calculado con él."""
```

(Only the last two lines of the existing dataclass are shown above for anchoring — do not duplicate them, just append the new block after the existing `residual_annual_COP` line.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_suite_epm_full.py -k S33_RiskInputCamposComponente -v`
Expected: PASS (2/2)

- [ ] **Step 5: Commit**

```bash
git add epm_core.py test_suite_epm_full.py
git commit -m "feat: agregar label/asset_type/source_asset_id a RiskInput (D-14)"
```

---

## Task 2: `AssetProfile` — campo `status`

Additive only, safe on its own.

**Files:**
- Modify: `epm_core.py:8956-8958` (end of `AssetProfile`, after `acceleration_factor`)
- Test: `test_suite_epm_full.py` (new test, can go in the same `S33` class from Task 1, or a dedicated one)

- [ ] **Step 1: Write the failing test**

Add to `test_suite_epm_full.py`:

```python
    def test_asset_profile_status_default_activo(self):
        a = AssetProfile(asset_id="A1", asset_name="Transformador 1",
                         asset_class="transformador", business_unit="TyD")
        self.assertEqual(a.status, "activo")

    def test_asset_profile_status_se_puede_declarar_retirado(self):
        a = AssetProfile(asset_id="A2", asset_name="Transformador 2",
                         asset_class="transformador", business_unit="TyD",
                         status="retirado")
        self.assertEqual(a.status, "retirado")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_suite_epm_full.py -k test_asset_profile_status -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'status'`

- [ ] **Step 3: Add the field**

In `epm_core.py`, `AssetProfile` dataclass (ends at line 8958 with `acceleration_factor: float = 2.0`), add as the LAST field (it must go last — every field before it already has a default, but appending after the last-defaulted field is always safe regardless):

```python
    acceleration_factor: float = 2.0       # Para modelo acelerado

    # ── D-14: soft-delete para el Registro de Activos ───────────────────────
    status: str = "activo"   # "activo" | "retirado"
    """Nunca se borra un AssetProfile, se retira (ver AssetRegistryStore.retire,
    Task 14). list() excluye retirados por defecto. Un activo reemplazado
    físicamente no debe desaparecer del Registro: si un proyecto pasado lo
    referenció (RiskInput.source_asset_id), debe poder auditarse contra qué
    datos se evaluó."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_suite_epm_full.py -k test_asset_profile_status -v`
Expected: PASS (2/2)

- [ ] **Step 5: Commit**

```bash
git add epm_core.py test_suite_epm_full.py
git commit -m "feat: agregar status (activo/retirado) a AssetProfile (D-14)"
```

---

## Task 3: `ProjectInput` — reemplazar `risk` por `risk_components` + `analysis_horizon_years`

**This is the breaking change.** After this task, nothing in the codebase that references `project.risk` will run — do not attempt to run the suite until Task 13. Proceed directly through Tasks 4-13.

**Files:**
- Modify: `epm_core.py:2103` (the `risk` field declaration)

- [ ] **Step 1: Replace the field**

In `epm_core.py`, `ProjectInput` dataclass, replace:

```python
    risk: Optional[RiskInput] = None
```

with:

```python
    risk_components: List[RiskInput] = field(default_factory=list)
    """Cero, uno o varios componentes de inacción — cada uno con su propia
    clasificación (matriz genérica / curva física / costo directo), calculados
    de forma independiente y sumados (ver ValueEngine._evaluate_inner).
    Reemplaza el campo 'risk' — reemplazo limpio, sin campo paralelo de
    compatibilidad (decisión explícita del usuario, ver spec §9)."""

    analysis_horizon_years: int = 0
    """Horizonte único del proyecto, declarado por el analista — alimenta
    beneficios, CADA componente de risk_components, EAV, VEI y Costo de
    Posponer. 0 = no declarado; evaluate() debe fallar explícitamente en ese
    caso (ver Task 5), sin fallback silencioso a ningún horizonte por
    defecto."""
```

`List` is already imported at the top of `epm_core.py` (used elsewhere, e.g. `AssetProfile.step_intervals: List[int]`) — no new import needed.

- [ ] **Step 2: Do not run tests yet.** Proceed to Task 4.

---

## Task 4: Migrar `test_suite_epm_full.py` — `_make_project` y los 10 sitios directos que sí evalúan

`_make_project` (line 81) is the shared factory used by the large majority of the 272 tests. Fixing it here restores construction for almost the whole suite in one edit. The remaining direct `ProjectInput(` construction sites are handled individually — most need no change (they never call `.evaluate()`), a few need an explicit, **dynamically-derived** horizon (never a hardcoded guess — see rationale below).

**Why "dynamically derived" and not a fixed default number:** different test classes build their `ValueEngine`/`PortfolioOptimizer` from different configs — some from `DEFAULT_CONFIG` implicitly, most from `_cfg()` (the real `config.json` on disk, merged). `config.json`'s `asset_horizons_by_bu._default` values (TyD=30, AyA=40, Gas=25, Generación=35) genuinely differ from `DEFAULT_CONFIG`'s hardcoded fallback values (TyD=15, AyA=30, Gas=30, Generación=25) — this exact `config.json`-vs-`DEFAULT_CONFIG` divergence is the same class of bug already registered as D-9/D-11/D-12. A single hardcoded table in `_make_project` would be silently wrong for half the suite. The safe pattern used throughout this task is: **wherever a test's own assertions depend on the specific horizon a project resolves to, derive it once via the still-intact `engine.get_horizon_for_asset(p)` method** (this method is NOT deleted by this migration — see Task 5 — it just stops being auto-invoked inside `evaluate()`), immediately after building the project, and assign it to `p.analysis_horizon_years`. This is self-correcting under whatever config is loaded and carries zero risk of a wrong guessed number.

**Files:**
- Modify: `test_suite_epm_full.py:81-118` (`_make_project`)
- Modify: `test_suite_epm_full.py` at lines 2134-2137 (`S21_TratamientoTemporal._proyecto`), 2264-2273 (`S21..._proyecto_directo` — no change needed, see below), 2376-2386 (`S22_RamaWeibull._proyecto`) + 2390-2403 (`_delta_r_esperado`), 2573-2586 (`S23_MILP_Nucleo._bueno`), 2745-2766 (`S24_MILP_Avanzado._proj`, 2809, 2831), 3210-3223 (`S26_CostoDelRetraso._p`), 3683-3695 and 3697-3725 (`S29_ProporcionalidadMetricas`, two direct tests), 3795-3819 (`S30_AnioOptimoCoherencia._proj`/`_riesgo`), and 12 sites across `S32_PreseleccionPorEAV` (4578-4843).
- No change needed: `test_suite_epm_full.py:3472` (`S28_MotorDeDependencias._p` — pure dependency-graph test, never calls `.evaluate()`); `test_suite_epm_full.py:4047,4057,4071` (`S31_PerfilCapexPorAnio` — pure `declared_execution_year` dataclass tests, never pass `risk=` and never need `analysis_horizon_years` for their assertions, though they DO need to still construct successfully post-Task-3, which they will since they never passed `risk=` in the first place).

- [ ] **Step 1: Fix `_make_project`**

Replace `test_suite_epm_full.py:81-118` with:

```python
def _make_project(
    pid="P001", name="Proyecto Test", bu="TyD",
    capex=10_000_000_000,
    inv_type="Reposicion de Activos",
    exec_year=2026, construction_years=1,
    drivers=None, risk=None,
    is_mandatory=False, is_committed=False, committed_year=0,
    asset_id="", opex_incremental=0.0,
    precedent_id="", min_lag=0,
    mutual_exclusion_ids=None, exclusion_group_id=None,
    require_outage_alignment=False,
    capex_by_year=None, declared_execution_year=0,
    analysis_horizon_years=10,
) -> ProjectInput:
    """analysis_horizon_years=10 es un default seguro y arbitrario, NO un
    intento de reproducir el horizonte que get_horizon_for_asset() habría
    resuelto antes de D-14 (eso depende de qué config usa cada test — ver
    nota en el plan de implementación). Los pocos tests cuyas aserciones sí
    dependen del valor exacto de horizonte fijan analysis_horizon_years
    explícitamente después de llamar a este factory."""
    if drivers is None:
        drivers = [_make_driver("VD-1", [_make_sc("VD-1-A", delta=100.0)])]
    p = ProjectInput(
        id=pid, name=name or pid,
        business_unit=bu,
        investment_type=inv_type,
        costs=CostInput(capex_COP=capex, opex_incremental_COP_year=opex_incremental,
                        capex_by_year=capex_by_year or {}),
        execution_year=exec_year,
        optimal_year=exec_year,
        drivers=drivers,
        risk_components=[risk] if risk else [],
        analysis_horizon_years=analysis_horizon_years,
        is_mandatory=is_mandatory,
        is_committed=is_committed,
        committed_year=committed_year,
        construction_years=construction_years,
        declared_execution_year=declared_execution_year,
    )
    p.asset_id = asset_id
    p.precedent_project_id = precedent_id
    p.min_lag_years = min_lag
    p.mutual_exclusion_ids = mutual_exclusion_ids or []
    p.exclusion_group_id = exclusion_group_id
    p.require_outage_alignment = require_outage_alignment
    return p
```

- [ ] **Step 2: `S21_TratamientoTemporal._proyecto` (line 2134-2137) — needs dynamic horizon**

This helper feeds a carefully-calibrated monotonicity meta-test (`test_vnt_no_crece_monotonamente_al_diferir` and its own "does this test still detect the historical bug" companion) that compares VNT across `deferral_years in range(0,13)` **per business unit**. `_make_project`'s flat default would give all 4 BUs the *same* horizon, whereas before this migration each BU resolved a *different* one via `config.json`'s `asset_horizons_by_bu` (TyD=30, AyA=40, Gas=25, Generación=35) — since this test's calibration was tuned against those real per-BU numbers, keep it exact:

Replace:
```python
        return _make_project(
            pid=f"DIF-{bu}", bu=bu, capex=5_000_000_000,
            drivers=[_make_driver("VD-1", [_make_sc("VD-1-A", delta=150.0)])],
            risk=_make_risk(3, 3, 1, 1), construction_years=1)
```
with:
```python
        p = _make_project(
            pid=f"DIF-{bu}", bu=bu, capex=5_000_000_000,
            drivers=[_make_driver("VD-1", [_make_sc("VD-1-A", delta=150.0)])],
            risk=_make_risk(3, 3, 1, 1), construction_years=1)
        p.analysis_horizon_years = ValueEngine(_cfg()).get_horizon_for_asset(p)
        return p
```

`S21`'s other helper, `_proyecto_directo` (lines 2264-2273, used by the "modo directo" tests), needs **no change** beyond the automatic fix from Step 1 — its tests only assert relative ordering of `horizon_used` across deferrals (`assertLess`), never an absolute number, so any consistent nonzero default is safe.

- [ ] **Step 3: `S22_RamaWeibull._proyecto` (lines 2376-2386) — needs dynamic horizon**

This class independently reimplements the Weibull ΔR formula (`_delta_r_esperado`, lines 2390-2403) and reads `c["asset_horizons_by_bu"][self.BU]["_default"]` directly from the real config — the engine's actual horizon must match that exactly, or this mutation-tested guardian class (documented in the file as catching 17/17 mutants) breaks.

Replace lines 2376-2386:
```python
    def _proyecto(self, beta=None, eta=None, edad=None, cb=None, cr=None,
                  pr=None, cy=0, capex=6_000_000_000):
        r = _make_risk(cb or self.CB, self.PB, cr or self.CR, pr or self.PR)
        r.weibull_beta = self.BETA if beta is None else beta
        r.weibull_eta = self.ETA if eta is None else eta
        r.asset_age_years = self.EDAD if edad is None else edad
        p = _make_project(pid="REP", bu=self.BU, capex=capex, inv_type=self.TIPO,
                          drivers=[_make_driver("VD-1", [_make_sc("VD-1-A", delta=50.0)])],
                          risk=r, construction_years=cy)
        p.asset_type = "_default"
        return p
```
with:
```python
    def _proyecto(self, beta=None, eta=None, edad=None, cb=None, cr=None,
                  pr=None, cy=0, capex=6_000_000_000):
        r = _make_risk(cb or self.CB, self.PB, cr or self.CR, pr or self.PR)
        r.weibull_beta = self.BETA if beta is None else beta
        r.weibull_eta = self.ETA if eta is None else eta
        r.asset_age_years = self.EDAD if edad is None else edad
        p = _make_project(pid="REP", bu=self.BU, capex=capex, inv_type=self.TIPO,
                          drivers=[_make_driver("VD-1", [_make_sc("VD-1-A", delta=50.0)])],
                          risk=r, construction_years=cy)
        p.asset_type = "_default"
        p.analysis_horizon_years = ValueEngine(self._cfg()).get_horizon_for_asset(p)
        return p
```

Then fix `_delta_r_esperado` (lines 2398-2403), which reads `p.risk.X` directly (bypassing `_make_project`'s internal wrapping) — replace every `p.risk.` with `p.risk_components[0].`:
```python
        cof_b = cl[str(p.risk_components[0].baseline_consequence_level)]["value_COP"]
        cof_r = cl[str(p.risk_components[0].residual_consequence_level)]["value_COP"]
        ratio = (pl[str(p.risk_components[0].residual_probability_level)]["annual_prob"] /
                 pl[str(p.risk_components[0].baseline_probability_level)]["annual_prob"])
        beta, eta = p.risk_components[0].weibull_beta, p.risk_components[0].weibull_eta
        edad = p.risk_components[0].asset_age_years
```

- [ ] **Step 4: `S23_MILP_Nucleo._bueno`/`_malo` (lines 2573-2586) — dynamic horizon, `risk=None` unaffected**

Replace lines 2580-2586:
```python
        return ProjectInput(
            id=pid, name=pid, business_unit=bu,
            investment_type="Reposicion de Activos", asset_type="_default",
            costs=CostInput(capex_COP=capex, opex_incremental_COP_year=0),
            drivers=[DriverInput(id="VD-1", reto="x", sub_criteria=[sc])],
            risk=None, execution_year=year, optimal_year=year,
            is_mandatory=mandatory, construction_years=cy)
```
with:
```python
        p = ProjectInput(
            id=pid, name=pid, business_unit=bu,
            investment_type="Reposicion de Activos", asset_type="_default",
            costs=CostInput(capex_COP=capex, opex_incremental_COP_year=0),
            drivers=[DriverInput(id="VD-1", reto="x", sub_criteria=[sc])],
            risk_components=[], execution_year=year, optimal_year=year,
            is_mandatory=mandatory, construction_years=cy)
        p.analysis_horizon_years = ValueEngine(self.cfg).get_horizon_for_asset(p)
        return p
```

- [ ] **Step 5: `S24_MILP_Avanzado._proj` (lines 2745-2766) — dynamic horizon**

`setUp` (lines 2747-2749) sets `self.cfg = _cfg()` and `self.opt = EnterpriseOptimizer(config=self.cfg)` — no bare `self.engine`, so build a throwaway `ValueEngine(self.cfg)` for the derivation, same as Step 4 did for `S23`. This class exercises the R8 MILP constraint directly (`test_r8_forzado_gana_sobre_alternativa_de_mayor_vnt`, `test_r8_solo_cuenta_si_baseline_alto_y_residual_bajo` — exactly the constraint rewritten in Task 10), always with a single risk profile per project, so wrapping into a 1-element `risk_components` list preserves identical behavior through Task 10's `max()`-over-components aggregation.

Replace lines 2756-2766:
```python
        p = ProjectInput(
            id=pid, name=pid, business_unit=bu,
            investment_type="Reposicion de Activos", asset_type="_default",
            costs=CostInput(capex_COP=capex, opex_incremental_COP_year=0),
            drivers=[DriverInput(id="VD-1", reto="x", sub_criteria=[sc])],
            risk=risk, execution_year=year, optimal_year=year,
            construction_years=1, is_committed=committed,
            committed_year=committed_year,
            require_outage_alignment=require_outage)
        p.asset_id = aid
        return p
```
with:
```python
        p = ProjectInput(
            id=pid, name=pid, business_unit=bu,
            investment_type="Reposicion de Activos", asset_type="_default",
            costs=CostInput(capex_COP=capex, opex_incremental_COP_year=0),
            drivers=[DriverInput(id="VD-1", reto="x", sub_criteria=[sc])],
            risk_components=[risk] if risk else [], execution_year=year, optimal_year=year,
            construction_years=1, is_committed=committed,
            committed_year=committed_year,
            require_outage_alignment=require_outage)
        p.asset_id = aid
        p.analysis_horizon_years = ValueEngine(self.cfg).get_horizon_for_asset(p)
        return p
```

The two `RiskInput(` construction sites (2809, 2831, building `riesgo`/`riesgo_no_mitiga` local variables passed in as this method's `risk=` argument) need no change themselves.

- [ ] **Step 6: `S26_CostoDelRetraso._p` (lines 3216-3223) — dynamic horizon**

This test's own docstring (`test_cod_usa_meses_no_anios_y_el_horizonte_del_activo`, line 3225) explicitly asserts `h_asset == 30` for `TyD` with no `asset_type` set — because `config.json`'s `asset_horizons_by_bu.TyD._default` is 30 and step-2 of `get_horizon_for_asset`'s lookup chain fires even with an empty `asset_type`. That assertion (line 3232-3234) calls `get_horizon_for_asset` directly and is **unaffected** by this migration (the method isn't deleted). But the NEXT line, `self.engine._calc_timing(p, benefits_npv)`, now reads `p.analysis_horizon_years` instead of re-deriving it — so `p` must carry that value explicitly.

Replace lines 3216-3223:
```python
    def _p(self, exec_year, optimal_year, drivers=None, construction_years=1):
        return ProjectInput(
            id="P", name="P", business_unit="TyD",
            investment_type="Reposicion de Activos",
            costs=CostInput(capex_COP=0, opex_incremental_COP_year=0.0),
            execution_year=exec_year, optimal_year=optimal_year,
            drivers=drivers or [], risk=None,
            construction_years=construction_years)
```
with:
```python
    def _p(self, exec_year, optimal_year, drivers=None, construction_years=1):
        p = ProjectInput(
            id="P", name="P", business_unit="TyD",
            investment_type="Reposicion de Activos",
            costs=CostInput(capex_COP=0, opex_incremental_COP_year=0.0),
            execution_year=exec_year, optimal_year=optimal_year,
            drivers=drivers or [], risk_components=[],
            construction_years=construction_years)
        p.analysis_horizon_years = self.engine.get_horizon_for_asset(p)
        return p
```

- [ ] **Step 7: `S29_ProporcionalidadMetricas` — two direct `ProjectInput(` sites (lines 3683-3687, 3707-3711)**

Both tests' docstrings say "TyD/_default mapea a 30 años" — set it explicitly rather than deriving it, since these tests are specifically designed to catch a horizon-related regression and should keep asserting against a literal, readable number that matches their own docstring:

At line 3683-3687, replace `risk=None` with `risk_components=[]`, and right after the `p = ProjectInput(...)` statement add `p.analysis_horizon_years = 30`.

At line 3707-3711, same change: `risk=None` → `risk_components=[]`, then `p.analysis_horizon_years = 30` right after construction. (Line 3719-3720 already independently asserts `get_horizon_for_asset(p) == 30` as a fixture self-check — leave that assertion as-is, it still exercises the untouched `get_horizon_for_asset` method directly.)

- [ ] **Step 8: `S30_AnioOptimoCoherencia._proj`/`_riesgo` (lines 3803-3819) — dynamic horizon**

This whole class exercises `optimal_timing_analysis` (migrated in Task 8) and several of its tests independently recompute the Weibull/exponential ΔR formula using `h_activo = self.engine.get_horizon_for_asset(p)` (lines 3881, 3932, 3969) — so `p.analysis_horizon_years` must equal exactly what `get_horizon_for_asset` resolves for `asset_type="transformador_potencia"`/`TyD`.

Replace lines 3810-3819:
```python
    def _proj(self, capex=6_000_000_000, cy=1, weibull=True, opex=50_000_000.0):
        sc = SubCriterionInput(id="VD-1-A", label="ENS", type="monetizable",
                               delta_annual=1000.0, price_per_unit=335_000.0)
        return ProjectInput(
            id="T", name="T", business_unit="TyD",
            investment_type="Reposicion de Activos",
            asset_type="transformador_potencia", construction_years=cy,
            costs=CostInput(capex_COP=capex, opex_incremental_COP_year=opex),
            drivers=[DriverInput(id="VD-1", reto="Calidad", sub_criteria=[sc])],
            risk=self._riesgo(weibull))
```
with:
```python
    def _proj(self, capex=6_000_000_000, cy=1, weibull=True, opex=50_000_000.0):
        sc = SubCriterionInput(id="VD-1-A", label="ENS", type="monetizable",
                               delta_annual=1000.0, price_per_unit=335_000.0)
        p = ProjectInput(
            id="T", name="T", business_unit="TyD",
            investment_type="Reposicion de Activos",
            asset_type="transformador_potencia", construction_years=cy,
            costs=CostInput(capex_COP=capex, opex_incremental_COP_year=opex),
            drivers=[DriverInput(id="VD-1", reto="Calidad", sub_criteria=[sc])],
            risk_components=[self._riesgo(weibull)])
        p.analysis_horizon_years = self.engine.get_horizon_for_asset(p)
        return p
```

- [ ] **Step 9: `S32_PreseleccionPorEAV` — 6 test methods, 12 `.asset_type =` sites need a matching `.analysis_horizon_years =`**

This class is the highest-risk one in the whole migration: its entire premise (documented in its own docstrings) is that two alternatives with **different** `asset_type` resolve to **different** horizons (30 years vs 10 years), which is exactly the mechanism `get_horizon_for_asset` implemented and this migration removes from the live evaluation path. `_preselect_by_eav()` itself needs no change (it calls `engine.evaluate(p)["eav_COP"]`, which after Task 5 already reads `p.analysis_horizon_years`) — but every test fixture that relies on `asset_type` to produce a horizon difference must now set that difference explicitly, or the tests silently stop testing anything (both alternatives would get the same default horizon, EAV would become monotonic with VNT, and the "the short-lived asset wins on EAV" premise would break at the very assertion the docstrings say guards against exactly that).

For **every** one of the following 12 lines, immediately after it add the paired `.analysis_horizon_years = self.engine.get_horizon_for_asset(<var>)` line:

| Line | Statement | Add immediately after |
|---|---|---|
| 4578 | `larga.asset_type = "transformador_potencia"  # TyD: 30 años` | `larga.analysis_horizon_years = self.engine.get_horizon_for_asset(larga)` |
| 4585 | `corta.asset_type = "sistema_control_SCADA"  # TyD: 10 años` | `corta.analysis_horizon_years = self.engine.get_horizon_for_asset(corta)` |
| 4727 | `larga.asset_type = "transformador_potencia"  # TyD: 30 años` | `larga.analysis_horizon_years = self.engine.get_horizon_for_asset(larga)` |
| 4734 | `corta.asset_type = "sistema_control_SCADA"  # TyD: 10 años` | `corta.analysis_horizon_years = self.engine.get_horizon_for_asset(corta)` |
| 4766 | `larga.asset_type = "transformador_potencia"` | `larga.analysis_horizon_years = self.engine.get_horizon_for_asset(larga)` |
| 4773 | `corta.asset_type = "sistema_control_SCADA"` | `corta.analysis_horizon_years = self.engine.get_horizon_for_asset(corta)` |
| 4797 | `larga.asset_type = "transformador_potencia"  # TyD: 30 años` | `larga.analysis_horizon_years = self.engine.get_horizon_for_asset(larga)` |
| 4804 | `corta.asset_type = "sistema_control_SCADA"  # TyD: 10 años` | `corta.analysis_horizon_years = self.engine.get_horizon_for_asset(corta)` |
| 4836 | `larga.asset_type = "transformador_potencia"` | `larga.analysis_horizon_years = self.engine.get_horizon_for_asset(larga)` |
| 4843 | `corta.asset_type = "sistema_control_SCADA"` | `corta.analysis_horizon_years = self.engine.get_horizon_for_asset(corta)` |

(Only 10 rows listed with certainty — re-verify against the file that there are exactly 6 test methods following this `larga`/`corta` pattern, since line numbers will have shifted slightly after Tasks 1-2's edits; search for `\.asset_type = "transformador_potencia"` and `\.asset_type = "sistema_control_SCADA"` in `S32_PreseleccionPorEAV` and pair every hit.)

`self.engine` is set in this class's `setUp` (`self.engine = ValueEngine(self.cfg)`) — reuse it, do not construct a new one, even in tests that also build a separate `opt = PortfolioOptimizer(...)`/`EnterpriseOptimizer(...)`.

`test_r3d_sigue_activo_si_se_desactiva_la_preseleccion` (lines 4693, 4700, both `.asset_type = "_default"`) does **not** need this fix — both alternatives get the same horizon either way, and the test doesn't depend on a horizon difference.

- [ ] **Step 10: Commit the test-suite fixture changes**

Do NOT run the suite yet — `_evaluate_inner` still references `project.risk` (Task 3 removed the field). Proceed to Task 5. Commit will happen once, at the end of Task 13.

---

## Task 5: Migrar `ValueEngine._evaluate_inner` — agregación de riesgo + horizonte único

**Files:**
- Modify: `epm_core.py:4127-4360` (`_evaluate_inner`, in place)

- [ ] **Step 1: Collapse the 3 redundant `get_horizon_for_asset(project)` calls into one read of the new field**

The current code calls `self.get_horizon_for_asset(project)` three times inside `_evaluate_inner` (lines 4135, 4175, 4286) — all three return the identical value (same `project`, same method, no side effects between calls), so they were already pure duplication. Replace all three with a single read at the top.

At line 4135, replace:
```python
        h_asset = self.get_horizon_for_asset(project)
```
with:
```python
        h_asset = project.analysis_horizon_years
        if h_asset <= 0:
            raise ValueError(
                f"Proyecto '{project.id}': analysis_horizon_years no fue declarado "
                f"(o es 0). No hay horizonte por defecto — el analista debe "
                f"declararlo explícitamente antes de evaluar (spec §8)."
            )
```

- [ ] **Step 2: Replace the risk block (lines 4159-4188) with the aggregation loop**

Replace:
```python
        # 2. Riesgo mitigado
        # Si hay delta_r_override_COP (portafolios no-Sostenimiento), se usa directamente.
        # De lo contrario, se calcula desde RiskInput (matriz 5×5) como antes.
        risk_result = {}
        delta_r_npv = 0.0
        _dr_override = getattr(project, "delta_r_override_COP", 0.0)
        if _dr_override != 0.0:
            delta_r_npv = _dr_override
            risk_result = {
                "baseline_annual_COP": 0.0,
                "residual_annual_COP": 0.0,
                "delta_r_annual_COP": 0.0,
                "delta_r_npv_COP": delta_r_npv,
                "source": "override_portafolio",
            }
        elif project.risk:
            h_asset = self.get_horizon_for_asset(project)
            g_asset = self._get_risk_growth_rate(
                project.business_unit,
                asset_type=getattr(project, "asset_type", ""),
            )
            risk_result = self._calc_risk(
                project.risk,
                construction_years=eff_cy,
                horizon=h_eval,
                business_unit=project.business_unit,
                risk_growth_rate=g_asset,
                deferral_years=deferral_years,
            )
            delta_r_npv = risk_result["delta_r_npv_COP"]
```
with:
```python
        # 2. Riesgo mitigado
        # Si hay delta_r_override_COP (portafolios no-Sostenimiento), se usa directamente.
        # De lo contrario, se agrega desde risk_components (D-14): cada componente
        # se calcula de forma independiente (matriz genérica / curva física / costo
        # directo) con _calc_risk(), sin cambios internos a esa función, y se suma.
        risk_result = {}
        delta_r_npv = 0.0
        _dr_override = getattr(project, "delta_r_override_COP", 0.0)
        if _dr_override != 0.0:
            delta_r_npv = _dr_override
            risk_result = {
                "baseline_annual_COP": 0.0,
                "residual_annual_COP": 0.0,
                "delta_r_annual_COP": 0.0,
                "delta_r_npv_COP": delta_r_npv,
                "source": "override_portafolio",
            }
        else:
            risk_components_results = []
            delta_r_annual_total = 0.0
            delta_r_npv_total = 0.0
            exposure_total = 0.0
            warnings_total: list = []
            for component in project.risk_components:
                g_comp = self._get_risk_growth_rate(
                    project.business_unit,
                    asset_type=getattr(component, "asset_type", ""),
                )
                r = self._calc_risk(
                    component,
                    construction_years=eff_cy,
                    horizon=h_eval,
                    business_unit=project.business_unit,
                    risk_growth_rate=g_comp,
                    deferral_years=deferral_years,
                )
                r["label"] = component.label
                r["source_asset_id"] = component.source_asset_id
                risk_components_results.append(r)
                delta_r_annual_total += r["delta_r_annual_COP"]
                delta_r_npv_total    += r["delta_r_npv_COP"]
                exposure_total       += r.get("deferral_exposure_COP", 0.0)
                warnings_total.extend(r.get("warnings", []))

            delta_r_npv = delta_r_npv_total
            _first = risk_components_results[0] if risk_components_results else {}
            risk_result = {
                # Compatibilidad: mismas claves que antes de D-14, ahora agregadas.
                # Con 0-1 componentes son idénticas al comportamiento previo; con
                # 2+, la suma es la agregación real. Las claves de forma/origen
                # (cof_source, pof_mode, etc.) reportan el PRIMER componente — no
                # existe un "agregado" con sentido único para ellas cuando cada
                # componente puede tomar una rama distinta (matriz/Weibull/directo).
                "baseline_annual_COP":   sum(r.get("baseline_annual_COP", 0.0) for r in risk_components_results),
                "residual_annual_COP":   sum(r.get("residual_annual_COP", 0.0) for r in risk_components_results),
                "delta_r_annual_COP":    delta_r_annual_total,
                "delta_r_npv_COP":       delta_r_npv_total,
                "deferral_exposure_COP": exposure_total,
                "horizon_used":          h_eval,
                "cof_source":            _first.get("cof_source"),
                "cof_mode":              _first.get("cof_mode"),
                "pof_mode":              _first.get("pof_mode"),
                "risk_growth_rate_used": _first.get("risk_growth_rate_used"),
                "weibull_beta":          _first.get("weibull_beta"),
                "weibull_eta":           _first.get("weibull_eta"),
                "asset_age_years":       _first.get("asset_age_years"),
                "pof_baseline_at_eval":  _first.get("pof_baseline_at_eval"),
                "tasa_falla_anual_base": _first.get("tasa_falla_anual_base"),
                "pof_residual_at_eval":  _first.get("pof_residual_at_eval"),
                "deferral_years":        deferral_years,
                "warnings":              warnings_total,
                "components":            risk_components_results,
            }
```

- [ ] **Step 3: Remove the third redundant call (line 4286, EAV/VEI horizon)**

Replace:
```python
        horizon_asset = self.get_horizon_for_asset(project)
```
with:
```python
        horizon_asset = h_asset
```

(`h_asset` was already established at Step 1 as `project.analysis_horizon_years`; do not call `get_horizon_for_asset` again here.)

- [ ] **Step 4: Update the result dict's `analysis_horizon_years` key**

At line 4341, replace:
```python
            "analysis_horizon_years": self.horizon,
```
with:
```python
            "analysis_horizon_years": project.analysis_horizon_years,
```
(This key previously reported the engine's global config fallback, which was misleading once a per-project horizon exists — now it reports what was actually used.)

- [ ] **Step 5: Add a guardian test for the empty-`risk_components` path (spec §8)**

Add to `test_suite_epm_full.py`, in `S1_ValueEngine`:

```python
    def test_risk_components_vacia_es_valida_y_no_aporta_riesgo(self):
        """spec §8: risk_components vacía es válida — equivalente al risk=None
        de antes de D-14, sin ΔR y sin reventar."""
        p = _make_project(pid="SINRIESGO", risk=None)
        r = self.engine.evaluate(p)
        self.assertEqual(r["risk_analysis"]["delta_r_npv_COP"], 0.0)
        self.assertEqual(r["risk_analysis"]["components"], [])
        self.assertEqual(r["delta_r_npv_COP"], 0.0)
```

Run: `python -m pytest test_suite_epm_full.py -k test_risk_components_vacia_es_valida -v` — expected PASS once Tasks 3-5 land (do not run in isolation yet, the suite isn't runnable until Task 13).

- [ ] **Step 6: Commit — not yet.** Proceed to Task 6.

---

## Task 6: Migrar `ValueEngine.evaluate_marginal` — horizonte para EAV/VEI marginal

**Files:**
- Modify: `epm_core.py:3546` (inside `evaluate_marginal`)

- [ ] **Step 1: Replace the horizon lookup**

Replace:
```python
            horizon  = self.get_horizon_for_asset(alternative)
```
with:
```python
            horizon  = alternative.analysis_horizon_years
```

(The `ValueError` guard for `horizon <= 0` already fires inside `evaluate()`/`evaluate_marginal`'s own call to `self.evaluate(alternative)` a few lines above, via Task 5 Step 1 — no need to duplicate the check here.)

---

## Task 7: Migrar `ValueEngine._calc_timing` — horizonte para Costo de Posponer

**Files:**
- Modify: `epm_core.py:4097` (inside `_calc_timing`)

- [ ] **Step 1: Replace the horizon lookup**

Replace:
```python
        h_asset = self.get_horizon_for_asset(project)
```
with:
```python
        h_asset = project.analysis_horizon_years
```

(`_calc_timing` is called from `_evaluate_inner` after the horizon guard already ran, so `h_asset` is guaranteed `> 0` here.)

---

## Task 8: Migrar `_optimal_timing_inner` — loop de componentes + horizonte

**Files:**
- Modify: `epm_core.py:4517-4708` (`_optimal_timing_inner`)

This function scans `t = 0..horizon_years` to find the optimal execution year, degrading risk year by year. It currently assumes a single `project.risk`. The spec did not enumerate this call site explicitly, but its own horizon-unification principle (§5) applies here too — this function is part of the core valuation path (feeds the wizard's "optimal_year" suggestion and `evaluate_with_timing`), not the Fleet-View exception the spec carved out in §2.

- [ ] **Step 1: Replace the horizon lookup (line 4541)**

Replace:
```python
        h_asset_timing = self.get_horizon_for_asset(project)
```
with:
```python
        h_asset_timing = project.analysis_horizon_years
        if h_asset_timing <= 0:
            raise ValueError(
                f"Proyecto '{project.id}': analysis_horizon_years no fue declarado "
                f"(o es 0) — optimal_timing_analysis() no puede escanear sin él."
            )
```

- [ ] **Step 2: Replace the single-risk degradation block (lines 4580-4628) with a per-component loop**

Replace:
```python
            # Escalar el riesgo por degradación — Weibull o exponencial
            if modified_project.risk:
                risk_inp = project.risk

                if risk_inp.weibull_beta > 0 and risk_inp.weibull_eta > 0:
                    # ── Rama Weibull: PoF(age + t) de la curva real ──────────
                    # Reutiliza la lógica de _calc_risk() con deferral=t para
                    # obtener ΔR_annual y ΔR_NPV integrados discretamente.
                    _risk_t = self._calc_risk(
                        risk_inp,
                        construction_years=project.construction_years,
                        horizon=h_asset_timing,
                        business_unit=project.business_unit,
                        risk_growth_rate=0.0,        # ignorado en modo Weibull
                        deferral_years=t,
                    )
                    degraded_delta_r_annual = _risk_t["delta_r_annual_COP"]
                    # _calc_risk() YA aplicó internamente el descuento por años
                    # de obra (su fd_lag). Aquí solo falta el del retraso de la
                    # decisión. Aplicar además fd_constr más abajo lo cobraba
                    # DOS VECES, y el defecto vivía solo en esta rama: la
                    # exponencial arma su NPV desde cero y sí lo necesita.
                    degraded_delta_r_npv    = _risk_t["delta_r_npv_COP"] * tf

                else:
                    # ── Rama exponencial ─────────────────────────────
                    original_risk_annual = self._calc_risk(
                        risk_inp,
                        business_unit=project.business_unit,
                    )["delta_r_annual_COP"]
                    degraded_delta_r_annual = original_risk_annual * ((1 + risk_growth_rate) ** t)
                    # Anualidad Gordon — consistente con _calc_risk() (C55 §4.3 + ).
                    _g  = risk_growth_rate
                    _r  = self.wacc
                    _h  = h_asset_timing
                    if abs(_r - _g) < 1e-9:
                        gordon_timing = float(_h)
                    elif _g > 0:
                        gordon_timing = (1.0 - ((1.0 + _g) / (1.0 + _r)) ** _h) / (_r - _g)
                    else:
                        gordon_timing = self._calc_factor_npv(_h)
                    # Esta rama construye el valor presente desde cero, así que
                    # aquí SÍ hay que aplicar los dos descuentos: el del retraso
                    # de la decisión y el de los años de obra.
                    degraded_delta_r_npv = (degraded_delta_r_annual * gordon_timing
                                            * tf * fd_constr)
            else:
                degraded_delta_r_annual = 0.0
                degraded_delta_r_npv    = 0.0
```
with:
```python
            # Escalar el riesgo por degradación — Weibull o exponencial —
            # sumado sobre TODOS los componentes de risk_components (D-14).
            degraded_delta_r_annual = 0.0
            degraded_delta_r_npv    = 0.0
            for risk_inp in project.risk_components:
                g_comp = self._get_risk_growth_rate(
                    project.business_unit,
                    asset_type=getattr(risk_inp, "asset_type", ""),
                )
                if risk_inp.weibull_beta > 0 and risk_inp.weibull_eta > 0:
                    # ── Rama Weibull: PoF(age + t) de la curva real ──────────
                    # Reutiliza la lógica de _calc_risk() con deferral=t para
                    # obtener ΔR_annual y ΔR_NPV integrados discretamente.
                    _risk_t = self._calc_risk(
                        risk_inp,
                        construction_years=project.construction_years,
                        horizon=h_asset_timing,
                        business_unit=project.business_unit,
                        risk_growth_rate=0.0,        # ignorado en modo Weibull
                        deferral_years=t,
                    )
                    comp_delta_r_annual = _risk_t["delta_r_annual_COP"]
                    # _calc_risk() YA aplicó internamente el descuento por años
                    # de obra (su fd_lag). Aquí solo falta el del retraso de la
                    # decisión. Aplicar además fd_constr más abajo lo cobraba
                    # DOS VECES, y el defecto vivía solo en esta rama: la
                    # exponencial arma su NPV desde cero y sí lo necesita.
                    comp_delta_r_npv    = _risk_t["delta_r_npv_COP"] * tf

                else:
                    # ── Rama exponencial ─────────────────────────────
                    original_risk_annual = self._calc_risk(
                        risk_inp,
                        business_unit=project.business_unit,
                    )["delta_r_annual_COP"]
                    comp_delta_r_annual = original_risk_annual * ((1 + g_comp) ** t)
                    # Anualidad Gordon — consistente con _calc_risk() (C55 §4.3 + ).
                    _g  = g_comp
                    _r  = self.wacc
                    _h  = h_asset_timing
                    if abs(_r - _g) < 1e-9:
                        gordon_timing = float(_h)
                    elif _g > 0:
                        gordon_timing = (1.0 - ((1.0 + _g) / (1.0 + _r)) ** _h) / (_r - _g)
                    else:
                        gordon_timing = self._calc_factor_npv(_h)
                    # Esta rama construye el valor presente desde cero, así que
                    # aquí SÍ hay que aplicar los dos descuentos: el del retraso
                    # de la decisión y el de los años de obra.
                    comp_delta_r_npv = (comp_delta_r_annual * gordon_timing
                                        * tf * fd_constr)

                degraded_delta_r_annual += comp_delta_r_annual
                degraded_delta_r_npv    += comp_delta_r_npv
```

Note: `risk_growth_rate` was a single value computed once per call to `_optimal_timing_inner` from `project.asset_type` (line 4531-4534) — it is now replaced per-component by `g_comp`, derived from each component's own `asset_type` (matching the pattern already used in Task 5). The outer `risk_growth_rate` parameter/local is no longer read inside this loop; leave its computation at the top of the function as-is (it's still used nowhere else in this function after this change — if your editor flags it unused, that's expected, do not delete the parameter itself since `optimal_timing_analysis` (the public wrapper) still passes it in).

- [ ] **Step 3: Fix the trailing single-risk reads (lines 4688-4705)**

Replace:
```python
        _pof_mode = ("weibull"
                     if (project.risk and project.risk.weibull_beta > 0
                         and project.risk.weibull_eta > 0)
                     else "exponential")

        return {
            "base_year":            base_year,
            "optimal_year":         optimal["year"],
            "optimal_delay_years":  optimal["delay_years"],
            "optimal_net_value":    optimal["net_value_COP"],
            "t0_net_value":         t0_result["net_value_COP"],
            "delta_vs_t0":          optimal["net_value_COP"] - t0_result["net_value_COP"],
            "urgency":              urgency,
            "pof_mode":             _pof_mode,               # "weibull" | "exponential"
            "risk_growth_rate":     risk_growth_rate if _pof_mode == "exponential" else None,
            "weibull_beta":         project.risk.weibull_beta if project.risk else None,
            "weibull_eta":          project.risk.weibull_eta  if project.risk else None,
            "asset_age_years":      project.risk.asset_age_years if project.risk else None,
            "capex_inflation_rate": capex_inflation_rate,
            "yearly_results":       yearly_results,
        }
```
with:
```python
        _first_risk = project.risk_components[0] if project.risk_components else None
        _pof_mode = ("weibull"
                     if (_first_risk and _first_risk.weibull_beta > 0
                         and _first_risk.weibull_eta > 0)
                     else "exponential")

        return {
            "base_year":            base_year,
            "optimal_year":         optimal["year"],
            "optimal_delay_years":  optimal["delay_years"],
            "optimal_net_value":    optimal["net_value_COP"],
            "t0_net_value":         t0_result["net_value_COP"],
            "delta_vs_t0":          optimal["net_value_COP"] - t0_result["net_value_COP"],
            "urgency":              urgency,
            "pof_mode":             _pof_mode,               # "weibull" | "exponential" — del PRIMER componente
            "risk_growth_rate":     risk_growth_rate if _pof_mode == "exponential" else None,
            "weibull_beta":         _first_risk.weibull_beta if _first_risk else None,
            "weibull_eta":          _first_risk.weibull_eta  if _first_risk else None,
            "asset_age_years":      _first_risk.asset_age_years if _first_risk else None,
            "capex_inflation_rate": capex_inflation_rate,
            "yearly_results":       yearly_results,
        }
```

(These trailing keys report the first component only, same "no aggregate makes sense for a mode/shape flag" reasoning as Task 5.)

---

## Task 9: Corregir bug pre-existente y migrar `SensitivityAnalyst._apply_proj_overrides`

**Files:**
- Modify: `epm_core.py:5969-5985`

**Root cause (verified, not guessed):** `RiskInput` has no field called `consequence_level` — the real fields are `baseline_consequence_level`/`residual_consequence_level` (confirmed against the dataclass at `epm_core.py:2026-2062`). This line has been dead-on-arrival since it was written: any sensitivity scenario passing `adjustments={"risk_multiplier": X}` with `X != 1.0` against a project carrying a risk profile would raise `AttributeError` the moment it executed. It is being touched here regardless, because `p.risk` no longer exists after Task 3 — fixing the underlying bug is not scope creep, the line cannot be migrated without deciding what it should actually do.

**Fix decision:** the surrounding comment says "Riesgo — se escala consecuencia del input de riesgo" (scale the *baseline* consequence — a sensitivity on risk severity should move the exposure being modeled, not the post-intervention residual, which reflects the mitigation itself and shouldn't be perturbed by a "what if the world's risk is worse" scenario). Apply `risk_mult` to `baseline_consequence_level` only, on every component, clamped to the matrix's valid range of 5, rounded to an int (the matrix does a string-keyed dict lookup on these levels — they must stay integers).

- [ ] **Step 1: Replace the buggy block**

Replace:
```python
            # Riesgo — se escala consecuencia del input de riesgo
            if risk_mult != 1.0 and p.risk:
                p.risk.consequence_level = min(
                    5.0, p.risk.consequence_level * risk_mult
                )
            return p
```
with:
```python
            # Riesgo — se escala la consecuencia BASE de cada componente
            # (D-14: bug pre-existente corregido — el campo 'consequence_level'
            # no existe en RiskInput desde siempre; esta línea nunca se había
            # ejercitado con éxito. El residual no se toca: refleja el efecto
            # de la propia mitigación, no la severidad del mundo externo que
            # este escenario de sensibilidad busca perturbar).
            if risk_mult != 1.0:
                for comp in (p.risk_components or []):
                    comp.baseline_consequence_level = min(
                        5, int(round(comp.baseline_consequence_level * risk_mult))
                    )
            return p
```

- [ ] **Step 2: Add a regression test proving the fix**

Add to `test_suite_epm_full.py`, in `S10_Sensitivity` (starts at line 1177 — re-check the exact line after Tasks 1-2's edits):

```python
    def test_risk_multiplier_escala_consecuencia_base_sin_reventar(self):
        """D-14: 'risk_multiplier' escalaba un campo que no existe
        (consequence_level) — reventaba con AttributeError en cuanto se
        usaba con un proyecto que tuviera riesgo. Ahora escala
        baseline_consequence_level de cada componente."""
        r = _make_risk(cb=3, pb=3, cr=1, pr=1)
        p = _make_project(pid="SENS1", risk=r)
        analyst = SensitivityAnalyst(_cfg())
        # No debe lanzar AttributeError (comportamiento previo a esta corrección).
        result = analyst.run_scenario([p], adjustments={"risk_multiplier": 1.5})
        self.assertIn("comparison", result)
        self.assertEqual(len(result["comparison"]), 1)
        self.assertEqual(result["comparison"][0]["id"], "SENS1")
```

- [ ] **Step 3: Run test to verify it passes**

Run: `python -m pytest test_suite_epm_full.py -k test_risk_multiplier_escala_consecuencia_base -v`
Expected: PASS (this test cannot meaningfully "fail first" in the usual TDD sense, since Task 3 already removed `.risk` — before Task 3 it would have failed with `AttributeError: consequence_level`; that historical failure is exactly what's documented above, not something to reproduce live).

---

## Task 10: Migrar `PortfolioOptimizer.optimize` — restricciones R8 y R6

**Files:**
- Modify: `epm_core.py:7407-7502`

Neither of these MILP constraint builders was mentioned in the spec — found by searching every `.risk` reference in the file. Both read a single risk profile per project to build a per-project scalar for a solver constraint; both need a defensible aggregation rule across `risk_components`, decided here since the spec never addressed them.

- [ ] **Step 1: R8 (Must-Mitigate) — worst-case component drives the constraint**

**Rationale:** R8 is a threshold/trigger constraint ("if baseline risk ≥ tolerance, force a mitigating project") — the right semantics for multiple components is that the constraint should fire if ANY component is above tolerance (worst case), and a project only counts as "mitigating" if it brings EVERY component below tolerance (residual must clear all of them).

Replace lines 7418-7427:
```python
            for i, proj in enumerate(projects):
                aid = getattr(proj, "asset_id", "") or ""
                if not aid or not proj.risk:
                    continue
                bl_level = self._risk_matrix_level(
                    proj.risk.baseline_consequence_level,
                    proj.risk.baseline_probability_level)
                re_level = self._risk_matrix_level(
                    proj.risk.residual_consequence_level,
                    proj.risk.residual_probability_level)
```
with:
```python
            for i, proj in enumerate(projects):
                aid = getattr(proj, "asset_id", "") or ""
                if not aid or not proj.risk_components:
                    continue
                # D-14: con varios componentes, el peor caso decide si el activo
                # está en riesgo inaceptable (baseline), y la mitigación solo
                # cuenta si baja TODOS los componentes por debajo del umbral
                # (residual) — de lo contrario un componente sin intervenir
                # seguiría siendo un riesgo inaceptable aunque otro sí se mitigue.
                bl_level = max(
                    self._risk_matrix_level(c.baseline_consequence_level,
                                            c.baseline_probability_level)
                    for c in proj.risk_components)
                re_level = max(
                    self._risk_matrix_level(c.residual_consequence_level,
                                            c.residual_probability_level)
                    for c in proj.risk_components)
```

- [ ] **Step 2: R6 (Riesgo máximo acumulado) — sum across components**

**Rationale:** R6 is a portfolio budget/cost constraint on accumulated risk exposure — sum is the correct aggregation, consistent with how `delta_r_npv` itself aggregates by summation in `_evaluate_inner` (Task 5).

Replace lines 7486-7496:
```python
                for i, proj in enumerate(projects):
                    # Riesgo base anual del proyecto (si tiene perfil de riesgo)
                    risk_proxy = proj.costs.capex_COP * 0.05  # 5% CAPEX = riesgo proxy
                    if proj.risk:
                        rm = self.config.get("risk_matrix", {})
                        cof_by_bu = rm.get("consequence_levels_by_bu", {})
                        cl = cof_by_bu.get(proj.business_unit) or rm.get("consequence_levels", {})
                        pl = rm.get("probability_levels", {})
                        c_val = cl.get(str(proj.risk.baseline_consequence_level), {}).get("value_COP", 0)
                        p_val = pl.get(str(proj.risk.baseline_probability_level), {}).get("annual_prob", 0)
                        risk_proxy = c_val * p_val
```
with:
```python
                for i, proj in enumerate(projects):
                    # Riesgo base anual del proyecto (si tiene perfil de riesgo)
                    risk_proxy = proj.costs.capex_COP * 0.05  # 5% CAPEX = riesgo proxy
                    if proj.risk_components:
                        # D-14: suma el proxy EAL de CADA componente — misma
                        # convención de agregación que delta_r_npv en evaluate().
                        rm = self.config.get("risk_matrix", {})
                        cof_by_bu = rm.get("consequence_levels_by_bu", {})
                        cl = cof_by_bu.get(proj.business_unit) or rm.get("consequence_levels", {})
                        pl = rm.get("probability_levels", {})
                        risk_proxy = 0.0
                        for c in proj.risk_components:
                            c_val = cl.get(str(c.baseline_consequence_level), {}).get("value_COP", 0)
                            p_val = pl.get(str(c.baseline_probability_level), {}).get("annual_prob", 0)
                            risk_proxy += c_val * p_val
```

- [ ] **Step 3: Search for any other `.risk` reference in `PortfolioOptimizer`/`EnterpriseOptimizer`**

Run:
```bash
grep -n "\.risk\b" epm_core.py
```
By this point in the migration, the only remaining hits should be the ones already handled in Tasks 5-10 plus Tasks 11-12 below — confirm there are none left inside `PortfolioOptimizer`/`EnterpriseOptimizer` beyond what Steps 1-2 fixed.

---

## Task 11: Migrar `ProjectWizard` (CLI) — `_capture_risk`/`run()` + prompt de horizonte

This is a separate, text-based CLI wizard inside `epm_core.py` (distinct from the Streamlit app) — the spec's §7 only addressed the Streamlit assistant, but this CLI wizard also constructs `ProjectInput(risk=...)` and must keep working.

**Files:**
- Modify: `epm_core.py:5091-5104` (`ProjectWizard.run`, the `ProjectInput(...)` construction)
- Modify: `epm_core.py:5039-5044` (the "6. Riesgo" prompt block, right before it)

- [ ] **Step 1: Add a horizon prompt**

Replace lines 5038-5044:
```python
        # 6. Riesgo
        risk_input = None
        print()
        print(c("  PERFIL DE RIESGO", Colors.BOLD))
        include_risk = input(c("  ¿Incluir análisis de riesgo? [S/n]: ", Colors.CYAN)).strip().lower()
        if include_risk != "n":
            risk_input = self._capture_risk()
```
with:
```python
        # 6. Riesgo
        risk_input = None
        print()
        print(c("  PERFIL DE RIESGO", Colors.BOLD))
        include_risk = input(c("  ¿Incluir análisis de riesgo? [S/n]: ", Colors.CYAN)).strip().lower()
        if include_risk != "n":
            risk_input = self._capture_risk()

        # 6b. Horizonte de análisis (D-14) — sin default precargado: el
        # analista debe declararlo, igual que en el asistente Streamlit.
        print()
        print(c("  HORIZONTE DE ANÁLISIS", Colors.BOLD))
        analysis_horizon = ask("Horizonte de análisis (años)", cast=int, required=True)
```

- [ ] **Step 2: Wire it into the `ProjectInput(...)` construction**

Replace line 5099 (`risk=risk_input,`) and add the horizon field, in the existing constructor call at lines 5091-5104:
```python
        proj_result = ProjectInput(
            id=pid, name=name,
            business_unit=bu,
            investment_type=inv_type,
            costs=CostInput(capex_COP=capex, opex_incremental_COP_year=opex),
            execution_year=exec_year,
            optimal_year=opt_year,
            drivers=driver_inputs,
            risk_components=[risk_input] if risk_input else [],
            analysis_horizon_years=analysis_horizon,
            dependencies=deps,
            is_mandatory=is_mandatory,
            construction_years=construction_years,
            benefit_start_offsets=benefit_offsets
        )
```

- [ ] **Step 3: (Optional, no test harness exists for this interactive CLI path)** If you want to sanity-check this compiles, run:
```bash
python -c "import epm_core"
```
Expected: no `SyntaxError`/`NameError` at import time (this doesn't exercise the interactive prompts, just confirms the module parses).

---

## Task 12: Migrar `PortfolioLoader._build_projects` — `risk_components` + resolución de horizonte

**Files:**
- Modify: `epm_core.py:10622-10673`

`PortfolioLoader` already holds `self._engine = ValueEngine(self.config)` (set in `__init__`, line 10415) — reuse it to resolve the horizon ONCE per imported row, calling the still-intact `get_horizon_for_asset`, so every bulk-imported project keeps exactly the same effective horizon it had before this migration (this loader never sets `asset_type` on imported projects today, so in practice this will resolve to each BU's `_default` entry or the global fallback — identical to current behavior, computed once instead of on every `evaluate()` call).

- [ ] **Step 1: Replace the risk construction**

Replace lines 10622-10635:
```python
            # Riesgo
            risk = None
            cb = g("cons_base"); pb = g("prob_base")
            cr = g("cons_res");  pr = g("prob_res")
            try:
                if cb and pb:
                    risk = RiskInput(
                        baseline_consequence_level=int(float(str(cb))),
                        baseline_probability_level=int(float(str(pb))),
                        residual_consequence_level=int(float(str(cr or 1))),
                        residual_probability_level=int(float(str(pr or 1))),
                    )
            except (ValueError, TypeError):
                pass
```
with:
```python
            # Riesgo
            risk = None
            cb = g("cons_base"); pb = g("prob_base")
            cr = g("cons_res");  pr = g("prob_res")
            try:
                if cb and pb:
                    risk = RiskInput(
                        baseline_consequence_level=int(float(str(cb))),
                        baseline_probability_level=int(float(str(pb))),
                        residual_consequence_level=int(float(str(cr or 1))),
                        residual_probability_level=int(float(str(pr or 1))),
                    )
            except (ValueError, TypeError):
                pass
```
(unchanged — `risk` stays a local variable here, only its consumption below changes)

- [ ] **Step 2: Replace the `ProjectInput(...)` construction**

Replace lines 10663-10673:
```python
            proj = ProjectInput(
                id=pid, name=name,
                business_unit=bu, investment_type=inv_type,
                costs=costs,
                execution_year=exec_yr, optimal_year=opt_yr,
                drivers=drivers, risk=risk,
                is_mandatory=is_mand,
                is_committed=is_committed_flag,
                committed_year=committed_yr,
                construction_years=cy,
            )
```
with:
```python
            proj = ProjectInput(
                id=pid, name=name,
                business_unit=bu, investment_type=inv_type,
                costs=costs,
                execution_year=exec_yr, optimal_year=opt_yr,
                drivers=drivers, risk_components=[risk] if risk else [],
                is_mandatory=is_mand,
                is_committed=is_committed_flag,
                committed_year=committed_yr,
                construction_years=cy,
            )
            # D-14: resuelto UNA vez al importar, con el mismo método que antes
            # se llamaba en cada evaluate() — preserva exactamente el horizonte
            # que este proyecto habría tenido antes de esta migración (este
            # importador nunca declara asset_type, así que hoy cae en el
            # _default de la BU o en el fallback global; get_horizon_for_asset
            # no se elimina — sigue existiendo para la Vista de Flota).
            proj.analysis_horizon_years = self._engine.get_horizon_for_asset(proj)
```

- [ ] **Step 3: Add a regression test**

Add to `S9_PortfolioLoader` (starts at line 1060 — re-check after prior edits):

```python
    def test_proyecto_importado_trae_analysis_horizon_years_resuelto(self):
        """D-14: el importador ya no puede depender de que evaluate() resuelva
        el horizonte por asset_type — debe quedar resuelto en el objeto."""
        loader = PortfolioLoader(config=DEFAULT_CONFIG)
        # Encabezados verificados contra _COL_ALIASES (epm_core.py:10147-10293):
        # "id", "name", "business_unit", "investment_type", "capex_total" y
        # "execution_year" son, cada uno, la propia clave interna (primer
        # alias de su lista), así que valen literalmente como encabezado.
        csv_bytes = (
            "id,name,business_unit,investment_type,capex_total,execution_year\n"
            "P001,Proyecto Test,TyD,Reposicion de Activos,1000000000,2026\n"
        ).encode("utf-8")
        projects, report = loader.from_bytes(csv_bytes, "test.csv")
        self.assertEqual(len(projects), 1)
        self.assertGreater(projects[0].analysis_horizon_years, 0,
                           "El proyecto importado debe traer un horizonte > 0, "
                           "o evaluate() fallará con el guardián del Task 5")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_suite_epm_full.py -k test_proyecto_importado_trae_analysis_horizon_years -v`
Expected: PASS

---

## Task 13: Ejecutar la suite completa y cerrar la migración

**Files:** none (verification only, plus the single commit for Tasks 1-12's remaining uncommitted work — Tasks 1-2 already committed themselves).

- [ ] **Step 1: Run the full suite**

```bash
python -m pytest test_suite_epm_full.py -v 2>&1 | tail -100
```

Expected: all tests pass. If any fail, the failure will be one of two kinds:

1. **`AttributeError`/`TypeError` mentioning `risk` or `risk_components`** — a call site was missed. Search for it: `grep -n "\.risk\b" epm_core.py test_suite_epm_full.py app_epm_aip_full_enterprise.py` and confirm every remaining hit is either `.risk_components`, `risk_growth_rate`, or a comment/docstring.
2. **A numeric assertion mismatch involving horizon/EAV/VEI/CoD/optimal_timing** — some test's fixture needed the "self-deriving horizon" pattern from Task 4 and wasn't caught by this plan's research. Apply the same pattern: after building the project in that test's fixture, add `p.analysis_horizon_years = <that test's own engine>.get_horizon_for_asset(p)` (or a literal number if the test's own docstring already states one, as in Task 4 Step 7) — never guess a number without deriving or reading it from the test's own stated premise.

Do not move on until the suite is fully green.

- [ ] **Step 2: Commit the whole migration atomically**

```bash
git add epm_core.py test_suite_epm_full.py
git commit -m "$(cat <<'EOF'
feat: reemplazar ProjectInput.risk por risk_components + analysis_horizon_years (D-14)

Reemplazo limpio, sin compatibilidad hacia atrás (decisión explícita del
usuario, ver spec). Migra los 6 usos de get_horizon_for_asset() dentro del
camino de evaluación (evaluate/evaluate_marginal/_calc_timing/
_optimal_timing_inner) al horizonte único declarado por el analista.
get_horizon_for_asset() y asset_horizons_by_bu NO se eliminan — siguen
sirviendo a la Vista de Flota (Auditoría), su único uso restante.

De paso: corrige un bug pre-existente en SensitivityAnalyst
(risk_multiplier escalaba un campo que no existe en RiskInput) y migra
las restricciones R8/R6 del MILP a agregar sobre múltiples componentes.
EOF
)"
```

---

## Task 14: `AssetRegistryStore` — clase nueva (núcleo mínimo del Registro de Activos)

Additive, independently TDD-able. Mirrors `SnapshotStore`'s one-file-per-record + `_index.json` pattern (verified at `epm_core.py:9661-10132`), using `dataclasses.asdict`/`AssetProfile(**data)` for serialization (the pattern `PlanVersionStore` already uses, at `epm_core.py:8730-8762`, rather than `SnapshotStore`'s manual `__dict__` walk — cleaner given `AssetProfile` has no nested non-primitive fields beyond two plain lists).

**Files:**
- Modify: `epm_core.py` — new class, placed after `SnapshotStore` ends (~line 10132, before `PortfolioLoader` at 10396 — verify the exact line after Tasks 1-13's edits and insert there)
- Test: `test_suite_epm_full.py` — new class `S34_AssetRegistryStore`

- [ ] **Step 1: Write the failing tests**

Add to `test_suite_epm_full.py`:

```python
class S34_AssetRegistryStore(unittest.TestCase):
    """Núcleo mínimo del Registro de Activos (D-14) — mismo patrón de
    persistencia que SnapshotStore: un archivo JSON por activo + _index.json."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.store = AssetRegistryStore(storage_dir=self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _activo(self, asset_id="AyA-TUB-001", **kw):
        defaults = dict(asset_id=asset_id, asset_name="Tubería sector norte",
                        asset_class="tuberia_ac", business_unit="AyA")
        defaults.update(kw)
        return AssetProfile(**defaults)

    def test_create_y_get(self):
        self.store.create(self._activo())
        recuperado = self.store.get("AyA-TUB-001")
        self.assertIsNotNone(recuperado)
        self.assertEqual(recuperado.asset_name, "Tubería sector norte")

    def test_create_con_id_existente_falla(self):
        self.store.create(self._activo())
        with self.assertRaises(ValueError):
            self.store.create(self._activo())

    def test_update_con_id_inexistente_falla(self):
        with self.assertRaises(ValueError):
            self.store.update(self._activo())

    def test_update_con_id_existente_reemplaza(self):
        self.store.create(self._activo())
        self.store.update(self._activo(asset_name="Tubería renombrada"))
        self.assertEqual(self.store.get("AyA-TUB-001").asset_name, "Tubería renombrada")

    def test_get_inexistente_devuelve_none(self):
        self.assertIsNone(self.store.get("NO_EXISTE"))

    def test_list_excluye_retirados_por_defecto(self):
        self.store.create(self._activo("A1", status="activo"))
        self.store.create(self._activo("A2", status="retirado"))
        ids = {a.asset_id for a in self.store.list()}
        self.assertEqual(ids, {"A1"})

    def test_list_include_retired_los_incluye(self):
        self.store.create(self._activo("A1", status="activo"))
        self.store.create(self._activo("A2", status="retirado"))
        ids = {a.asset_id for a in self.store.list(include_retired=True)}
        self.assertEqual(ids, {"A1", "A2"})

    def test_list_filtra_por_business_unit(self):
        self.store.create(self._activo("A1", business_unit="AyA"))
        self.store.create(self._activo("A2", business_unit="TyD"))
        ids = {a.asset_id for a in self.store.list(business_unit="TyD")}
        self.assertEqual(ids, {"A2"})

    def test_search_substring_en_id_o_nombre(self):
        self.store.create(self._activo("AyA-TUB-001", asset_name="Tubería norte"))
        self.store.create(self._activo("AyA-TUB-002", asset_name="Tubería sur"))
        self.store.create(self._activo("AyA-BOM-001", asset_name="Bomba central"))
        resultados = {a.asset_id for a in self.store.search("norte")}
        self.assertEqual(resultados, {"AyA-TUB-001"})
        resultados2 = {a.asset_id for a in self.store.search("TUB")}
        self.assertEqual(resultados2, {"AyA-TUB-001", "AyA-TUB-002"})

    def test_retire_marca_status_sin_borrar_archivo(self):
        self.store.create(self._activo("A1"))
        self.store.retire("A1")
        activo = self.store.get("A1")
        self.assertEqual(activo.status, "retirado")
        self.assertNotIn("A1", {a.asset_id for a in self.store.list()})

    def test_import_csv_importa_filas_validas(self):
        import io
        csv_content = (
            "asset_id,asset_name,asset_class,business_unit,install_year\n"
            "AyA-TUB-010,Tubería importada,tuberia_ac,AyA,2010\n"
            "AyA-TUB-011,Otra tubería,tuberia_ac,AyA,2015\n"
        )
        importados, errores = self.store.import_csv(io.StringIO(csv_content))
        self.assertEqual(len(importados), 2)
        self.assertEqual(len(errores), 0)
        self.assertIsNotNone(self.store.get("AyA-TUB-010"))

    def test_import_csv_fila_con_id_duplicado_contra_el_registro_es_error(self):
        import io
        self.store.create(self._activo("AyA-TUB-020"))
        csv_content = (
            "asset_id,asset_name,asset_class,business_unit,install_year\n"
            "AyA-TUB-020,Duplicado,tuberia_ac,AyA,2010\n"
        )
        importados, errores = self.store.import_csv(io.StringIO(csv_content))
        self.assertEqual(len(importados), 0)
        self.assertEqual(len(errores), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest test_suite_epm_full.py -k S34_AssetRegistryStore -v
```
Expected: FAIL — `NameError: name 'AssetRegistryStore' is not defined`

- [ ] **Step 3: Implement `AssetRegistryStore`**

Insert into `epm_core.py`, right after `SnapshotStore`'s class body ends (find the exact line by searching for the start of `class PortfolioLoader` and inserting the new class immediately before it):

```python
class AssetRegistryStore:
    """
    Registro de Activos — núcleo mínimo (D-14).

    Mismo patrón de persistencia que SnapshotStore: un archivo JSON por
    registro + un _index.json liviano para listar/buscar sin abrir cada
    archivo. A diferencia de SnapshotStore (append-only, ids generados con
    uuid4), aquí el asset_id lo declara el analista — create()/update() son
    simétricos y ninguno sobrescribe en silencio.
    """

    def __init__(self, storage_dir: str = "./activos_registrados"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.storage_dir / "_index.json"
        self._index = self._load_index()

    def _load_index(self) -> dict:
        if self._index_path.exists():
            with open(self._index_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"assets": [], "created": datetime.now(timezone.utc).isoformat()}

    def _save_index(self) -> None:
        with open(self._index_path, 'w', encoding='utf-8') as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)

    def _asset_path(self, asset_id: str) -> Path:
        return self.storage_dir / f"{asset_id}.json"

    def _write(self, asset: AssetProfile) -> None:
        with open(self._asset_path(asset.asset_id), 'w', encoding='utf-8') as f:
            json.dump(dataclasses.asdict(asset), f, ensure_ascii=False, indent=2)

    def _update_index_entry(self, asset: AssetProfile) -> None:
        entries = [e for e in self._index["assets"] if e["asset_id"] != asset.asset_id]
        entries.append({
            "asset_id": asset.asset_id,
            "asset_name": asset.asset_name,
            "business_unit": asset.business_unit,
            "asset_class": asset.asset_class,
            "status": asset.status,
        })
        self._index["assets"] = entries
        self._save_index()

    def create(self, asset: AssetProfile) -> None:
        """Falla si asset_id ya existe — sin sobrescritura silenciosa."""
        if self._asset_path(asset.asset_id).exists():
            raise ValueError(
                f"El activo '{asset.asset_id}' ya existe en el Registro — "
                f"usa update() para modificarlo."
            )
        self._write(asset)
        self._update_index_entry(asset)

    def update(self, asset: AssetProfile) -> None:
        """Falla si asset_id NO existe — simetría con create()."""
        if not self._asset_path(asset.asset_id).exists():
            raise ValueError(
                f"El activo '{asset.asset_id}' no existe en el Registro — "
                f"usa create() para darlo de alta."
            )
        self._write(asset)
        self._update_index_entry(asset)

    def get(self, asset_id: str) -> Optional[AssetProfile]:
        path = self._asset_path(asset_id)
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return AssetProfile(**data)

    def list(self, business_unit: str = None, asset_class: str = None,
             include_retired: bool = False) -> List[AssetProfile]:
        result = []
        for entry in self._index["assets"]:
            if not include_retired and entry["status"] == "retirado":
                continue
            if business_unit and entry["business_unit"] != business_unit:
                continue
            if asset_class and entry["asset_class"] != asset_class:
                continue
            asset = self.get(entry["asset_id"])
            if asset is not None:
                result.append(asset)
        return result

    def search(self, query: str) -> List[AssetProfile]:
        """Substring simple en asset_id o asset_name, insensible a mayúsculas."""
        q = query.lower()
        return [
            a for a in self.list(include_retired=True)
            if q in a.asset_id.lower() or q in a.asset_name.lower()
        ]

    def retire(self, asset_id: str) -> None:
        """status = 'retirado'. NO borra el archivo."""
        asset = self.get(asset_id)
        if asset is None:
            raise ValueError(f"El activo '{asset_id}' no existe en el Registro.")
        asset.status = "retirado"
        self.update(asset)

    def import_csv(self, file_obj) -> Tuple[List[AssetProfile], List[dict]]:
        """(importados, errores_por_fila). Cada fila con asset_id ya
        existente (en el Registro o repetido dentro del mismo archivo) es un
        error reportado, no una sobrescritura — misma regla que create().

        Reutiliza el mismo normalizador de encabezados (_norm) que usa
        PortfolioLoader, con su propia tabla de sinónimos — no reutiliza
        _map_columns porque esa está fija a la tabla de alias de proyectos,
        no de activos (mismo patrón que ActualsLoader._map_cols)."""
        import csv as _csv
        reader = _csv.DictReader(file_obj)
        col_map = _map_asset_columns(reader.fieldnames or [])

        importados: List[AssetProfile] = []
        errores: List[dict] = []
        seen_in_file: set = set()

        for i, row in enumerate(reader, start=2):  # fila 1 = encabezado
            def g(field):
                col = col_map.get(field)
                return row.get(col, "").strip() if col else ""

            asset_id = g("asset_id")
            if not asset_id:
                errores.append({"fila": i, "motivo": "asset_id vacío o no mapeado"})
                continue
            if asset_id in seen_in_file or self._asset_path(asset_id).exists():
                errores.append({"fila": i, "asset_id": asset_id,
                               "motivo": "asset_id duplicado — usar update() o corregir el archivo"})
                continue
            try:
                asset = AssetProfile(
                    asset_id=asset_id,
                    asset_name=g("asset_name") or asset_id,
                    asset_class=g("asset_class"),
                    business_unit=g("business_unit"),
                    install_year=int(g("install_year") or 2000),
                )
            except (ValueError, TypeError) as e:
                errores.append({"fila": i, "asset_id": asset_id, "motivo": str(e)})
                continue
            self.create(asset)
            seen_in_file.add(asset_id)
            importados.append(asset)

        return importados, errores


_COL_ALIASES_ASSET = {
    "asset_id":       ["asset_id", "id_activo", "id"],
    "asset_name":     ["asset_name", "nombre_activo", "nombre"],
    "asset_class":    ["asset_class", "clase_activo", "familia", "tipo_activo"],
    "business_unit":  ["business_unit", "unidad_negocio", "bu", "negocio"],
    "install_year":   ["install_year", "anio_instalacion", "año_instalacion"],
}


def _map_asset_columns(columns: List[str]) -> Dict[str, str]:
    """Mismo patrón que PortfolioLoader._map_columns, tabla de alias propia
    de activos (ver docstring de AssetRegistryStore.import_csv)."""
    norm_cols = {_norm(c): c for c in columns}
    mapping: Dict[str, str] = {}
    for field_name, aliases in _COL_ALIASES_ASSET.items():
        for alias in aliases:
            if _norm(alias) in norm_cols:
                mapping[field_name] = norm_cols[_norm(alias)]
                break
    return mapping
```

`_norm` is already a module-level function (`epm_core.py:10324`, per this plan's research) — no new helper needed for it, just reuse it. Place `_COL_ALIASES_ASSET`/`_map_asset_columns` either right before the `AssetRegistryStore` class or right after it — either is fine, just keep them adjacent to the class that uses them.

`dataclasses` is already imported at module level (used by `PlanVersionStore`) — no new import needed. `Tuple`/`Dict`/`Optional`/`List` are already imported (used throughout the file).

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest test_suite_epm_full.py -k S34_AssetRegistryStore -v
```
Expected: PASS (13/13)

- [ ] **Step 5: Commit**

```bash
git add epm_core.py test_suite_epm_full.py
git commit -m "feat: agregar AssetRegistryStore — nucleo minimo del Registro de Activos (D-14)"
```

---

## Task 15: Wizard Streamlit — Paso 1: Horizonte de análisis + Familia de Activo condicional

**Files:**
- Modify: `app_epm_aip_full_enterprise.py:3204-3296` (Familia de Activo block) and a new insertion near it for the horizon field.

- [ ] **Step 1: Add the "Horizonte de análisis" field**

Right after the "Tipo de Inversión" selectbox block (`app_epm_aip_full_enterprise.py:3072-3076`), insert:

```python
            with _r1b:
                _w_inv_i = st.selectbox("Tipo de Inversión",
                    range(len(_inv_keys)), format_func=lambda i: _inv_keys[i],
                    index=_prev_inv_i, key="wi_inv")
                _w_inv = _inv_keys[_w_inv_i]

            # ── D-14: Horizonte de análisis — único por proyecto, sin
            # sugerencia precargada. El analista debe declararlo explícitamente;
            # no hay fallback silencioso a ningún horizonte por defecto (ver
            # spec §7, §9 — mismo principio de "sin default automático" que
            # el resto de este diseño).
            _prev_horizon = st.session_state.get("w_analysis_horizon_years", None)
            _w_horizon = st.number_input(
                "Horizonte de análisis (años)",
                min_value=1, max_value=100,
                value=_prev_horizon if _prev_horizon else None,
                step=1, key="wi_analysis_horizon",
                help="Horizonte único que alimenta beneficios, cada componente "
                     "de inacción, EAV, VEI y Costo de Posponer. Sin sugerencia "
                     "precargada — decláralo con criterio propio.",
                placeholder="Años (obligatorio)",
            )
```

(`value=None` with a Streamlit `number_input` requires `st.number_input`'s newer signature that accepts `None` as "empty" — if the Streamlit version pinned in this project predates that support, use `value=_prev_horizon if _prev_horizon else 0` instead and treat `0` as "not yet declared" consistently with the engine-side guard from Task 5 Step 1; check the Streamlit version in `requirements.txt`/the venv before choosing.)

- [ ] **Step 2: Make "Familia de Activo" conditional on `Tipo de Inversión == "Reposición de Activos"`**

Wrap the existing block at `app_epm_aip_full_enterprise.py:3204-3296` (from the `st.markdown('<div style="height:10px">...')` line through wherever the Weibull-defaults caption ends) in a conditional. Read the exact current end-line of that block yourself before editing (this plan's research read through line 3296 as the section header comment boundary, but the block's real end must be re-verified against the file at implementation time — the section clearly continues past what was quoted in this plan's research). The shape of the edit:

```python
            if _w_inv == "Reposición de Activos":
                # ── Familia de activo + nivel de datos (Data Maturity C55) ───────
                st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
                # ... (todo el bloque existente, re-indentado un nivel) ...
            else:
                # Crecimiento/Expansión no tiene un activo físico que envejezca
                # — no aplica Familia de Activo. w_asset_family/w_data_level
                # siguen siendo leídos más abajo (Paso 2/3), así que deben
                # quedar con un valor válido igual.
                _af_sel_key = "otro"
                _af_meta    = ASSET_FAMILIES["otro"]
                st.session_state["w_asset_family"] = "otro"
                # w_data_level: para Crecimiento/Expansión el dato no viene de
                # una curva de degradación — usar "driver_based" como default
                # de este branch, consistente con el resto del asistente.
                st.session_state.setdefault("w_data_level", "driver_based")
```

Before finalizing this task, re-read `app_epm_aip_full_enterprise.py:3204-3330` yourself to confirm the exact variables consumed downstream (`_af_sel_key`, `_af_meta`, `w_asset_family`, `w_data_level` were the ones this plan's research identified as read later, at lines 3326-3327, 3377-3379, 5590, 5942 — re-verify those line numbers too, since Tasks 1-14 don't touch this file but this task itself will shift line numbers as you edit it).

- [ ] **Step 3: Manual verification in the browser**

Start the app (`streamlit run app_epm_aip_full_enterprise.py`), go to Paso 1, and confirm:
- The "Horizonte de análisis" field is present and empty by default.
- Selecting "Tipo de Inversión" = "Reposición de Activos" shows "Familia de Activo".
- Selecting "Tipo de Inversión" = "Crecimiento / Expansión" hides "Familia de Activo" and does not crash when advancing to Paso 2/3.

- [ ] **Step 4: Commit**

```bash
git add app_epm_aip_full_enterprise.py
git commit -m "feat: horizonte de análisis en Paso 1 + Familia de Activo condicional a Reposición (D-14)"
```

---

## Task 16: Streamlit — página de administración del Registro de Activos

The wizard (Task 17) lets an analyst create an asset ad-hoc while building a risk component, "ahí mismo" per spec §7 — but spec §2/§6.2 also describe bulk CSV/Excel import and manual alta as their own standalone Registry capabilities, independent of being mid-wizard. This task adds a dedicated management view so an analyst can populate/browse/retire the Registry without going through a project evaluation at all.

**Files:**
- Modify: `app_epm_aip_full_enterprise.py` — add a new tab/section alongside the existing ones (e.g. next to "Auditoría" in whatever top-level `st.tabs(...)`/sidebar navigation this app already uses — read the file's top-level navigation structure yourself before picking the insertion point, since this plan's research did not audit the full tab list).

- [ ] **Step 1: Add the tab and a listing table**

```python
def render_registro_activos():
    st.header("Registro de Activos")
    _registry = AssetRegistryStore()

    _tab_lista, _tab_alta, _tab_importar = st.tabs(["Buscar / Listar", "Alta manual", "Importar CSV/Excel"])

    with _tab_lista:
        _busq = st.text_input("Buscar (ID o nombre)", key="ra_busqueda")
        _bu_filtro = st.selectbox("Unidad de Negocio", ["Todas", "TyD", "AyA", "Gas", "Generacion"], key="ra_bu_filtro")
        _incluir_retirados = st.checkbox("Incluir retirados", key="ra_incluir_retirados")
        if _busq:
            _activos = _registry.search(_busq)
        else:
            _bu_arg = None if _bu_filtro == "Todas" else _bu_filtro
            _activos = _registry.list(business_unit=_bu_arg, include_retired=_incluir_retirados)
        if _activos:
            st.dataframe(pd.DataFrame([
                {"ID": a.asset_id, "Nombre": a.asset_name, "Clase": a.asset_class,
                 "BU": a.business_unit, "Año instalación": a.install_year,
                 "Estado": a.status}
                for a in _activos
            ]), use_container_width=True, hide_index=True)
            _retirar_id = st.selectbox("Retirar activo", options=[""] + [a.asset_id for a in _activos if a.status == "activo"], key="ra_retirar_sel")
            if _retirar_id and st.button("Confirmar retiro", key="ra_retirar_btn"):
                _registry.retire(_retirar_id)
                st.success(f"Activo '{_retirar_id}' retirado.")
                st.rerun()
        else:
            st.info("Sin activos que coincidan con el filtro.")

    with _tab_alta:
        with st.form("ra_form_alta"):
            _new_id = st.text_input("ID del activo")
            _new_name = st.text_input("Nombre")
            _new_bu = st.selectbox("Unidad de Negocio", ["TyD", "AyA", "Gas", "Generacion"], key="ra_alta_bu")
            _af_keys_ra = list(ASSET_FAMILIES.keys())
            _new_class = st.selectbox("Familia / Clase", options=_af_keys_ra,
                                       format_func=lambda k: ASSET_FAMILIES[k]["label"], key="ra_alta_clase")
            _new_year = st.number_input("Año de instalación", min_value=1950, max_value=datetime.now().year, value=2010)
            _af_ra = ASSET_FAMILIES.get(_new_class, ASSET_FAMILIES["otro"])
            _wbd_ra = _af_ra.get("weibull_defaults") or {"beta": 3.0, "eta": 30.0}
            _new_beta = st.number_input("β", min_value=0.1, max_value=10.0, value=float(_wbd_ra.get("beta", 3.0)), step=0.1)
            _new_eta = st.number_input("η (años)", min_value=1.0, max_value=200.0, value=float(_wbd_ra.get("eta", 30.0)), step=1.0)
            _new_condition = st.slider("Condición actual (0=nuevo, 1=falla inminente)", 0.0, 1.0, 0.5)
            if st.form_submit_button("Crear activo"):
                try:
                    _registry.create(AssetProfile(
                        asset_id=_new_id, asset_name=_new_name, asset_class=_new_class,
                        business_unit=_new_bu, install_year=int(_new_year),
                        weibull_beta=_new_beta, weibull_eta=_new_eta,
                        current_condition=_new_condition,
                    ))
                    st.success(f"Activo '{_new_id}' creado.")
                except ValueError as e:
                    st.error(str(e))

    with _tab_importar:
        _archivo = st.file_uploader("Archivo CSV", type=["csv"], key="ra_importar_file")
        if _archivo is not None and st.button("Importar", key="ra_importar_btn"):
            import io as _io
            _importados, _errores = _registry.import_csv(
                _io.StringIO(_archivo.getvalue().decode("utf-8")))
            st.success(f"{len(_importados)} activos importados.")
            if _errores:
                st.warning(f"{len(_errores)} filas con error:")
                st.dataframe(pd.DataFrame(_errores), use_container_width=True, hide_index=True)
```

Wire `render_registro_activos()` into wherever this app's top-level navigation dispatches to tab-render functions (mirror however the existing "Auditoría" tab is wired — read that call site before adding this one).

- [ ] **Step 2: Manual verification in the browser**

Create an asset via "Alta manual," confirm it appears in "Buscar / Listar"; retire it, confirm it disappears from the default list and reappears with "Incluir retirados" checked; import a small CSV with 2 valid rows + 1 duplicate-ID row, confirm 2 imported / 1 error reported.

- [ ] **Step 3: Commit**

```bash
git add app_epm_aip_full_enterprise.py
git commit -m "feat: pagina de administracion del Registro de Activos (D-14)"
```

---

## Task 17: Wizard Streamlit — reemplazar "Do Nothing" por "Componentes de Inacción"

This is the largest UI task. The current wizard builds exactly ONE `RiskInput` at the "Confirmar Do Nothing" button (`app_epm_aip_full_enterprise.py:5187-5241`) from whichever single branch was active (`asset_aware` Weibull inputs, or the generic matrix). It needs to become a list, built incrementally, each item independently typed.

**Files:**
- Modify: `app_epm_aip_full_enterprise.py:5187-5241` (confirm-button block) and the surrounding Paso 3 "FASE A" section (from ~3573).

- [ ] **Step 1: Introduce session-state storage for the component list**

Near the top of the Paso 3 / "FASE A" section (before the existing `_w_meth` branch at line 3579), add:

```python
        # ── D-14: Componentes de Inacción — lista compartida entre alternativas ──
        if "w_risk_components" not in st.session_state:
            st.session_state["w_risk_components"] = []   # list of dicts: {id, label, tipo, ...campos...}
```

- [ ] **Step 2: Add a "component type" selector, reusing the existing branch logic per-component**

Replace the single unconditional `if _w_meth == "asset_aware": ... else: ...` top-level branch (lines 3579/4486) with a per-component loop. Each component gets its own `tipo` (`"activo_fisico"` / `"matriz_generica"` / `"costo_directo"`), stored alongside its own widget values keyed by a component id (e.g., `f"comp_{n}"`) instead of the current global `wi_dn_*`/`wi_b_*` keys.

Concretely, structure it as:

```python
        st.markdown("### Componentes de Inacción")
        st.caption(
            "Qué cuesta NO actuar. Puede haber más de uno — por ejemplo, dos "
            "activos físicos distintos degradándose en el mismo proyecto, o un "
            "activo físico más un costo directo de demanda no atendida."
        )

        for idx, comp in enumerate(st.session_state["w_risk_components"]):
            with st.expander(f"Componente {idx + 1}: {comp.get('label') or '(sin nombre)'}", expanded=False):
                comp["label"] = st.text_input(
                    "Nombre del componente", value=comp.get("label", ""),
                    key=f"wi_comp_{comp['id']}_label",
                    placeholder="ej. 'Tubería sector norte', 'Costo directo expansión'",
                )
                comp["tipo"] = st.radio(
                    "Tipo", options=["activo_fisico", "matriz_generica", "costo_directo"],
                    format_func=lambda t: {
                        "activo_fisico": "Activo físico del Registro",
                        "matriz_generica": "Nivel de matriz genérico",
                        "costo_directo": "Costo directo",
                    }[t],
                    index=["activo_fisico", "matriz_generica", "costo_directo"].index(comp.get("tipo", "matriz_generica")),
                    key=f"wi_comp_{comp['id']}_tipo", horizontal=True,
                )

                if comp["tipo"] == "activo_fisico":
                    _registry = AssetRegistryStore()
                    _buscar = st.text_input("Buscar activo en el Registro", key=f"wi_comp_{comp['id']}_buscar")
                    _candidatos = _registry.search(_buscar) if _buscar else _registry.list(business_unit=_w_bu)
                    _opciones = {a.asset_id: f"{a.asset_id} — {a.asset_name}" for a in _candidatos}
                    _opciones["__nuevo__"] = "➕ Crear activo nuevo (no guardar al Registro todavía)"
                    _sel = st.selectbox("Activo", options=list(_opciones.keys()),
                                       format_func=lambda k: _opciones[k],
                                       key=f"wi_comp_{comp['id']}_asset_sel")
                    if _sel != "__nuevo__":
                        _activo = _registry.get(_sel)
                        comp["source_asset_id"] = _activo.asset_id
                        comp["asset_type"] = _activo.asset_class
                        _af = ASSET_FAMILIES.get(_activo.asset_class, ASSET_FAMILIES["otro"])
                        _wbd = _af.get("weibull_defaults") or {"beta": 3.0, "eta": 30.0}
                        comp["beta"] = _activo.weibull_beta or _wbd.get("beta", 3.0)
                        comp["eta"] = _activo.weibull_eta or _wbd.get("eta", 30.0)
                        comp["edad"] = float(datetime.now().year - _activo.install_year)
                    else:
                        comp["source_asset_id"] = ""
                        _af_keys_c = list(ASSET_FAMILIES.keys())
                        comp["asset_type"] = st.selectbox(
                            "Familia de activo", options=_af_keys_c,
                            format_func=lambda k: ASSET_FAMILIES[k]["label"],
                            key=f"wi_comp_{comp['id']}_family")
                        _af = ASSET_FAMILIES.get(comp["asset_type"], ASSET_FAMILIES["otro"])
                        _wbd = _af.get("weibull_defaults") or {"beta": 3.0, "eta": 30.0}
                        comp["beta"] = st.number_input("β", min_value=0.1, max_value=10.0,
                            value=float(comp.get("beta", _wbd.get("beta", 3.0))), step=0.1,
                            key=f"wi_comp_{comp['id']}_beta")
                        _eta_default = float(comp.get("eta", _wbd.get("eta", 30.0)))
                        comp["eta"] = st.number_input("η (años)", min_value=1.0,
                            max_value=max(200.0, _eta_default + 50.0), value=_eta_default,
                            step=1.0, key=f"wi_comp_{comp['id']}_eta")
                        comp["edad"] = st.number_input("Edad actual (años)", min_value=0.0,
                            value=float(comp.get("edad", 0.0)), step=1.0,
                            key=f"wi_comp_{comp['id']}_edad")
                    comp["cof_mm"] = st.number_input("Consecuencia de falla (MM COP)",
                        min_value=0.0, value=float(comp.get("cof_mm", 1.0)), step=1.0,
                        key=f"wi_comp_{comp['id']}_cof")
                    comp["baseline_prob_level"] = st.select_slider(
                        "Nivel de probabilidad base (matriz 1-5)", options=[1, 2, 3, 4, 5],
                        value=comp.get("baseline_prob_level", 3),
                        key=f"wi_comp_{comp['id']}_pof_level")

                elif comp["tipo"] == "matriz_generica":
                    comp["baseline_cons_level"] = st.select_slider(
                        "Consecuencia base (1-5)", options=[1, 2, 3, 4, 5],
                        value=comp.get("baseline_cons_level", 3),
                        key=f"wi_comp_{comp['id']}_bc")
                    comp["baseline_prob_level"] = st.select_slider(
                        "Probabilidad base (1-5)", options=[1, 2, 3, 4, 5],
                        value=comp.get("baseline_prob_level", 3),
                        key=f"wi_comp_{comp['id']}_bp")

                else:  # costo_directo
                    comp["baseline_annual_COP"] = st.number_input(
                        "Costo anual de NO intervenir (MM COP)", min_value=0.0,
                        value=float(comp.get("baseline_annual_COP", 0.0)) / 1e6, step=1.0,
                        key=f"wi_comp_{comp['id']}_dir_base") * 1e6

                if st.button("Eliminar componente", key=f"wi_comp_{comp['id']}_del"):
                    st.session_state["w_risk_components"] = [
                        c for c in st.session_state["w_risk_components"] if c["id"] != comp["id"]
                    ]
                    st.rerun()

        if st.button("+ Agregar componente de inacción", key="wi_add_component"):
            import uuid as _uuid
            st.session_state["w_risk_components"].append({
                "id": str(_uuid.uuid4())[:8], "label": "", "tipo": "matriz_generica",
            })
            st.rerun()
```

This keeps every widget already present in the file (β/η/edad/CoF for asset-aware, consequence/probability sliders for the generic matrix) but re-keys them per-component and adds the previously-missing "costo directo" branch (confirmed in this plan's research: `baseline_annual_COP`/`residual_annual_COP` had zero UI anywhere in this file before this task).

- [ ] **Step 3: Build the `risk_components` list at confirm time**

Replace the single `RiskInput(...)` construction at lines 5207-5225 with a loop building one `RiskInput` per entry in `st.session_state["w_risk_components"]`:

```python
                if st.button("Confirmar Componentes de Inacción →", type="primary",
                             use_container_width=True, key="wi_set_base"):
                    _risk_components_built = []
                    for comp in st.session_state["w_risk_components"]:
                        if comp["tipo"] == "costo_directo":
                            _r = RiskInput(
                                baseline_consequence_level=1, baseline_probability_level=1,
                                baseline_annual_COP=comp.get("baseline_annual_COP", 0.0),
                                label=comp.get("label", ""),
                            )
                        elif comp["tipo"] == "activo_fisico":
                            _pof_level = comp.get("baseline_prob_level", 3)
                            _r = RiskInput(
                                baseline_consequence_level=_cof_to_level(comp.get("cof_mm", 1.0)),
                                baseline_probability_level=_pof_level,
                                residual_consequence_level=_cof_to_level(comp.get("cof_mm", 1.0)),
                                residual_probability_level=_pof_level,
                                weibull_beta=comp.get("beta", 0.0),
                                weibull_eta=comp.get("eta", 0.0),
                                asset_age_years=comp.get("edad", 0.0),
                                label=comp.get("label", ""),
                                asset_type=comp.get("asset_type", ""),
                                source_asset_id=comp.get("source_asset_id", ""),
                            )
                        else:  # matriz_generica
                            _bc = comp.get("baseline_cons_level", 3)
                            _bp = comp.get("baseline_prob_level", 3)
                            _r = RiskInput(
                                baseline_consequence_level=_bc, baseline_probability_level=_bp,
                                residual_consequence_level=_bc, residual_probability_level=_bp,
                                label=comp.get("label", ""),
                            )
                        _risk_components_built.append(_r)

                    _ts_base = datetime.now().strftime("%H%M%S")
                    _base_proj = ProjectInput(
                        id=f"BASE-{_w_year}-{_ts_base}",
                        name=f"Do Nothing — {_w_name}",
                        # ... (todos los demás argumentos que ya existían, sin cambio) ...
                        risk_components=_risk_components_built,
                        analysis_horizon_years=_w_horizon,
                    )
```

Read the full existing constructor call (`app_epm_aip_full_enterprise.py:5190-5225`) yourself before editing — only `risk=RiskInput(...)` is being replaced by `risk_components=_risk_components_built` plus the new `analysis_horizon_years=_w_horizon` (from Task 15's field); every other keyword argument in that constructor call is untouched.

`_cof_to_level` already exists in this file (confirmed in this plan's research, used at line ~3799-3801) — reuse it, do not redefine it.

- [ ] **Step 4: Manual verification in the browser**

Start the app, go through Paso 1 → Paso 2 → Paso 3, and confirm:
- "+ Agregar componente de inacción" adds an expander.
- Each of the 3 tipos renders its own distinct widgets.
- "Eliminar componente" removes only that entry.
- Clicking "Confirmar Componentes de Inacción →" with 2 components (e.g., one `activo_fisico` + one `costo_directo`) does not crash, and the resulting baseline project's evaluation shows a nonzero `delta_r_npv_COP` reflecting both.

- [ ] **Step 5: Commit**

```bash
git add app_epm_aip_full_enterprise.py
git commit -m "feat: reemplazar Do Nothing por Componentes de Inacción en el asistente (D-14)"
```

---

## Task 18: Wizard Streamlit — declaración obligatoria de residual por alternativa × componente

**Files:**
- Modify: `app_epm_aip_full_enterprise.py` — the per-alternative "Fase B" section (around lines 5868+, where `RiskInput` per alternative is currently built at line 6694).

- [ ] **Step 1: Pre-fill identity, force blank residual, per component**

Replace the current single per-alternative `RiskInput` construction (`app_epm_aip_full_enterprise.py:6694-6703`) with a loop mirroring `st.session_state["w_risk_components"]`, copying `label`/`tipo`/`source_asset_id`/`asset_type`/baseline values from the shared list, but leaving every residual field genuinely blank (`value=None` on the widget, not a pre-filled `1`) until the analyst enters it:

```python
                _letter = chr(65 + _w_cur_idx)
                st.markdown(f"#### Declarar residual — Alternativa {_letter}")
                st.caption(
                    "Cada componente de inacción requiere su propio valor "
                    "residual para esta alternativa — sin valor por defecto. "
                    "Un componente sin declarar bloquea el cálculo de esta "
                    "alternativa (ver spec §7, §9: no se repite el default "
                    "automático de nivel 1 que hoy infla resultados si el "
                    "analista olvida un campo)."
                )
                _residuals_ok = True
                _risk_components_alt = []
                for comp in st.session_state["w_risk_components"]:
                    st.markdown(f"**{comp.get('label') or '(sin nombre)'}**")
                    if comp["tipo"] == "costo_directo":
                        _res_val = st.number_input(
                            f"Costo anual residual tras {_letter} (MM COP)",
                            min_value=0.0, value=None, key=f"wi_alt{_letter}_{comp['id']}_res_dir",
                            placeholder="Obligatorio — sin valor por defecto",
                        )
                        if _res_val is None:
                            _residuals_ok = False
                            continue
                        _r = RiskInput(
                            baseline_consequence_level=1, baseline_probability_level=1,
                            baseline_annual_COP=comp.get("baseline_annual_COP", 0.0),
                            residual_annual_COP=_res_val * 1e6,
                            label=comp.get("label", ""),
                        )
                    else:
                        _res_cons = st.select_slider(
                            f"Consecuencia residual tras {_letter} (1-5)",
                            options=[None, 1, 2, 3, 4, 5],
                            value=None, key=f"wi_alt{_letter}_{comp['id']}_rc",
                        )
                        _res_prob = st.select_slider(
                            f"Probabilidad residual tras {_letter} (1-5)",
                            options=[None, 1, 2, 3, 4, 5],
                            value=None, key=f"wi_alt{_letter}_{comp['id']}_rp",
                        )
                        if _res_cons is None or _res_prob is None:
                            _residuals_ok = False
                            continue
                        if comp["tipo"] == "activo_fisico":
                            _r = RiskInput(
                                baseline_consequence_level=_cof_to_level(comp.get("cof_mm", 1.0)),
                                baseline_probability_level=comp.get("baseline_prob_level", 3),
                                residual_consequence_level=_res_cons,
                                residual_probability_level=_res_prob,
                                weibull_beta=comp.get("beta", 0.0),
                                weibull_eta=comp.get("eta", 0.0),
                                asset_age_years=comp.get("edad", 0.0),
                                label=comp.get("label", ""),
                                asset_type=comp.get("asset_type", ""),
                                source_asset_id=comp.get("source_asset_id", ""),
                            )
                        else:
                            _r = RiskInput(
                                baseline_consequence_level=comp.get("baseline_cons_level", 3),
                                baseline_probability_level=comp.get("baseline_prob_level", 3),
                                residual_consequence_level=_res_cons,
                                residual_probability_level=_res_prob,
                                label=comp.get("label", ""),
                            )
                    _risk_components_alt.append(_r)

                if not _residuals_ok:
                    st.warning(
                        "Faltan valores residuales por declarar — completa todos "
                        "los componentes antes de continuar."
                    )
```

- [ ] **Step 2: Block the "Agregar Alt." confirm button when residuals are incomplete**

Wherever the existing "Agregar Alt." button is defined (immediately following the block above, consuming `_risk_components_alt`), guard it:

```python
                if st.button(f"Agregar Alternativa {_letter} →", type="primary",
                             disabled=not _residuals_ok, key=f"wi_add_alt_{_letter}"):
                    ...
                    _risk_input_alt = _risk_components_alt   # ya es la lista completa
                    ...
```

Read the existing button/construction block (`app_epm_aip_full_enterprise.py:6694-6870`, per this plan's research) yourself and replace its `risk=_risk_input_alt` (singular) with `risk_components=_risk_components_alt` at both the provisional (`~6709-6729`) and definitive (`~6754+`) `ProjectInput(...)` constructions — both need the same `analysis_horizon_years=_w_horizon` addition as Task 17 Step 3.

- [ ] **Step 3: Manual verification in the browser**

Confirm: with 2 components in "Componentes de Inacción," advancing to Alternativa A shows both, each requiring its own residual; leaving one blank keeps "Agregar Alternativa A" disabled; filling all residuals enables it.

- [ ] **Step 4: Commit**

```bash
git add app_epm_aip_full_enterprise.py
git commit -m "feat: forzar declaracion explicita de residual por alternativa x componente (D-14)"
```

---

## Task 19: Wizard Streamlit — resultados: desglose por componente de inacción

**Files:**
- Modify: `app_epm_aip_full_enterprise.py` — wherever the existing results/Dashboard section renders `delta_r_npv_COP` (confirmed sites: lines 2468, 2471, 6800, 7162, 7346, 7807 per this plan's research) — add ONE new display near the most detailed of these (line ~7346, the per-alternative comparison table), not all six.

- [ ] **Step 1: Add a per-component breakdown table**

Near `app_epm_aip_full_enterprise.py:7346` (`_dr = _bar.get("delta_r_npv_COP", 0) / 1e6`), after whatever existing display uses `_dr`, add:

```python
                _componentes = _bar.get("risk_analysis", {}).get("components", [])
                if len(_componentes) > 1:
                    with st.expander(f"Desglose por componente de inacción ({len(_componentes)})"):
                        st.dataframe(pd.DataFrame([
                            {
                                "Componente": c.get("label") or f"Componente {i+1}",
                                "Activo de origen": c.get("source_asset_id") or "—",
                                "ΔR (MM COP)": f"{c.get('delta_r_npv_COP', 0) / 1e6:+,.1f}",
                            }
                            for i, c in enumerate(_componentes)
                        ]), use_container_width=True, hide_index=True)
```

This only renders when there are 2+ components (a single-component project's breakdown would just repeat the top-level `delta_r_npv_COP` number already shown, adding no information).

`pd` (pandas) is already imported and used throughout this file (confirmed via the existing `st.dataframe(pd.DataFrame(...))` patterns nearby) — no new import needed.

- [ ] **Step 2: Manual verification in the browser**

Evaluate a baseline with 2+ components (built in Task 16), confirm the "Desglose por componente de inacción" expander appears and shows both rows with plausible ΔR figures summing to the total already shown elsewhere on the page.

- [ ] **Step 3: Commit**

```bash
git add app_epm_aip_full_enterprise.py
git commit -m "feat: mostrar desglose por componente de inaccion en resultados (D-14)"
```

---

## Task 20: Registrar D-14 en `REGISTRO_ASUNTOS_ABIERTOS.md`

**Files:**
- Modify: `REGISTRO_ASUNTOS_ABIERTOS.md`

- [ ] **Step 1: Read the file's existing D-12/D-13 entries first**

Read `REGISTRO_ASUNTOS_ABIERTOS.md` in full (it's referenced throughout this codebase's history but was not re-read during this plan's research) to match its exact section/table formatting before adding D-14 — do not guess the format, copy the structure of the D-13 entry exactly.

- [ ] **Step 2: Add the D-14 narrative section**

Following the D-13 entry's format exactly, add a new section summarizing:
- Origin: calibration-workshop questions from AyA ("¿no está la opción mixta?", "¿no podían existir varios activos degradándose?").
- What changed: `ProjectInput.risk` → `risk_components: List[RiskInput]` (summed aggregation); `analysis_horizon_years` replaces the asset-type-driven horizon for the 6 call sites inside the evaluation path (list them: `_evaluate_inner` ×3 collapsed to 1, `evaluate_marginal`, `_calc_timing`, `_optimal_timing_inner`); `AssetRegistryStore` (núcleo mínimo) plus its own admin page (buscar/listar, alta manual, importar CSV/Excel); wizard UI replacing "Do Nothing" with "Componentes de Inacción" plus forced explicit residual declaration per alternative × component.
- Side fixes made during the migration (link them explicitly since they're easy to lose track of): the `SensitivityAnalyst._apply_proj_overrides` pre-existing dead bug (Task 9), the R8/R6 MILP constraint aggregation rules (Task 10).
- Reference: link to the spec (`docs/superpowers/specs/2026-09-04-componentes-multiples-inaccion-registro-activos-design.md`) and this plan (`docs/superpowers/plans/2026-09-04-componentes-multiples-inaccion-registro-activos.md`).

- [ ] **Step 3: Add Trazabilidad rows**

Following whatever tabular "Trazabilidad" format D-12/D-13 used, add rows for: the dataclass change, the engine migration, `AssetRegistryStore` + su página de administración, y el cambio de UI del asistente (Componentes de Inacción + declaración de residual) — matching the granularity D-13's rows used (4 rows for a comparably-sized change; this one may warrant 5 given the extra admin-page row).

- [ ] **Step 4: Commit**

```bash
git add REGISTRO_ASUNTOS_ABIERTOS.md
git commit -m "docs: registrar D-14 — componentes multiples de inaccion, registro de activos, horizonte unico"
```

---

## Final check

- [ ] Run the full suite one more time end-to-end: `python -m pytest test_suite_epm_full.py -v 2>&1 | tail -30` — expect all green (272 original + ~2 new in Task 1 + ~2 in Task 2 + 1 in Task 9 + 1 in Task 12 + 13 in Task 14 ≈ 291-292 tests).
- [ ] Confirm `get_horizon_for_asset` and `asset_horizons_by_bu` are still present and unchanged in `epm_core.py` (they must NOT be deleted — they still serve the Fleet View / Auditoría tab, per spec §2).
- [ ] Start the Streamlit app once more and walk the full wizard flow (Paso 1 → Paso 2 → Paso 3 → confirmar Componentes de Inacción → Alternativa A con residual → resultados) for both `Tipo de Inversión` values, confirming no crash and a sensible VNT.
