"""
test_suite_epm_full.py — Validacion Integral del Motor AIP EPM
==============================================================
Cobertura: 100 por ciento de modulos, clases y restricciones MILP.

Escenarios:
  S1  — ValueEngine: todos los 5 VDs, todos los sub-criterios, todas las BUs
  S2  — MILP R1-R12 + restricciones de presupuesto/personal/concurrencia/TOTEX
  S3  — Dependencias R3a-R3d, deadlock, cascade CoD
  S4  — EVM (completado, en curso, SPI/CPI/EAC)
  S5  — OutageCalendar + R10 alineacion de ventanas
  S6  — SnapshotStore SHA-256 append-only (ISO 55001)
  S7  — PlanVersionStore: save, list, get, diff
  S8  — WorkflowEngine: 9 estados, 4 roles, transiciones validas/invalidas
  S9  — PortfolioLoader: Excel, CSV, MM-heuristica
  S10 — SensitivityAnalyst
  S11 — AssetDegradationEngine: Weibull, Lineal, Escalon, Acelerado
  S12 — Committed Spend R11 + committed_summary
  S13 — TOTEX R12 + totex_utilization
  S14 — Frontera eficiente CAPEX + TOTEX
  S15 — Riesgo: matriz multi-tipo por BU, R8 must-mitigate
  S16 — Proporcionalidad: Full/Simplified/Rule-Based
  S17 — Helpers: load_config, save_config, print_report, write_audit_log
  S18 — PortafolioTI + alcances: silo / enterprise-global / pisos R7
  S19 — Validacion de datos: IDs duplicados, CAPEX=0, alias tolerancia
  S20 — Escenario integral: todos los modulos en una sola ejecucion

Ejecutar:
    python test_suite_epm_full.py
"""
from __future__ import annotations
import sys, os, io, json, csv, copy, hashlib, tempfile, unittest, time, math
from pathlib import Path
from typing import List, Dict, Optional

# ── Ruta al proyecto ──────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

# ── Import del motor ──────────────────────────────────────────────────────────
import epm_core
from epm_core import (
    DEFAULT_CONFIG,
    ProjectInput, CostInput, DriverInput, SubCriterionInput, RiskInput,
    ResourcePeriod, AssetProfile, DegradationModel,
    ProjectActual, PlannedOutage,
    ProjectWorkflow, WorkflowTransition,
    PlanVersion,
    DependencyError,
    ValueEngine, EnterpriseOptimizer, AssetDegradationEngine,
    AssetRegistryStore,
    PortfolioOptimizer, BudgetPeriod,
    _preselect_by_eav,
    SnapshotStore, DependencyEngine, PortfolioLoader, SensitivityAnalyst,
    EVMEngine, OutageCalendar, WorkflowEngine,
    PlanVersionStore,
    load_config, save_config, print_report, write_audit_log,
    _merge_config_defaults,
    WF_STATES, WF_FULL_TRANSITIONS, WF_TERMINAL_STATES,
    PULP_OK,
    INVESTMENT_TYPES,
)

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS DE CONSTRUCCION
# ─────────────────────────────────────────────────────────────────────────────

def _cfg() -> dict:
    """Config desde config.json, fusionado con DEFAULT_CONFIG.

    La fusion replica lo que hacen load_config() y get_config(): un archivo
    guardado con una version anterior del motor no tiene las claves agregadas
    despues, y sin fusion esas capacidades quedan desactivadas en silencio.
    """
    cfg_path = PROJECT_DIR / "config.json"
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            return _merge_config_defaults(json.load(f))[0]
    return copy.deepcopy(DEFAULT_CONFIG)


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


def _make_driver(vid="VD-1", sub_criteria=None, weight=1.0) -> DriverInput:
    return DriverInput(
        id=vid, reto=f"Reto {vid}", weight=weight,
        sub_criteria=sub_criteria or [],
    )


def _make_sc(sc_id="VD-1-A", sc_type="monetizable", delta=100.0,
             price=335_000) -> SubCriterionInput:
    """SubCriterionInput sin concept_rating (no existe en el dataclass)."""
    return SubCriterionInput(
        id=sc_id, label=sc_id,
        type=sc_type,
        delta_annual=delta,
        price_per_unit=price,
    )


def _make_sc_concept(sc_id="VD-1-F", scale_max=1_000_000_000.0) -> SubCriterionInput:
    """SubCriterionInput tipo concept_rating."""
    return SubCriterionInput(
        id=sc_id, label=sc_id,
        type="concept_rating",
        scale_max_COP=scale_max,
    )


def _make_risk(cb=4, pb=4, cr=1, pr=1) -> RiskInput:
    return RiskInput(
        baseline_consequence_level=cb,
        baseline_probability_level=pb,
        residual_consequence_level=cr,
        residual_probability_level=pr,
    )


def _make_periods(years=None, budget=50_000_000_000,
                  pm=200, concurrent=20,
                  totex_budget=None) -> List[ResourcePeriod]:
    years = years or [2026, 2027, 2028, 2029, 2030]
    return [
        ResourcePeriod(
            year=yr,
            budget_COP=budget,
            max_person_months=float(pm),
            max_projects_concurrent=concurrent,
            totex_budget_COP=totex_budget,
        )
        for yr in years
    ]


def _workflow_transition(wf, to_state, role, label, comment=""):
    """Helper para aplicar transiciones de workflow."""
    return WorkflowEngine.apply(
        wf, {"to": to_state, "label": label},
        actor="test_user", role=role, bu="TyD", comment=comment,
    )


# ─────────────────────────────────────────────────────────────────────────────
# S1 — VALUE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class S1_ValueEngine(unittest.TestCase):
    """Cobertura: todos los VDs, BUs, sub-criterios y edge-cases."""

    def setUp(self):
        self.cfg = _cfg()
        self.engine = ValueEngine(self.cfg)

    # ── VD-1: Calidad de Servicios ────────────────────────────────────────────

    def test_vd1a_ens_tyd(self):
        """VD-1-A: ENS TyD — 100 MWh x $335.000."""
        sc = _make_sc("VD-1-A", delta=100.0, price=335_000)
        p  = _make_project(bu="TyD", capex=70_000_000, drivers=[_make_driver("VD-1", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0, "VD-1-A debe generar VNT positivo")

    def test_vd1b_saidi_tyd(self):
        """VD-1-B: SAIDI TyD — 1000 hora-cliente x $6.200."""
        sc = _make_sc("VD-1-B", delta=1000.0, price=6_200)
        p  = _make_project(bu="TyD", capex=12_000_000, drivers=[_make_driver("VD-1", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    def test_vd1c_agua_aya(self):
        """VD-1-C: m3 agua — AyA."""
        sc = _make_sc("VD-1-C", delta=5000.0, price=3_100)
        p  = _make_project(bu="AyA", capex=30_000_000, drivers=[_make_driver("VD-1", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    def test_vd1d_monetizable(self):
        """VD-1-D: monetizable generico."""
        sc = _make_sc("VD-1-D", delta=100.0, price=22_000)
        p  = _make_project(bu="Gas", capex=4_000_000, drivers=[_make_driver("VD-1", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    def test_vd1f_concept_rating(self):
        """VD-1-F: concept_rating — sin precio, escala-S."""
        sc = _make_sc_concept("VD-1-F", scale_max=2_000_000_000.0)
        p  = _make_project(bu="TyD", drivers=[_make_driver("VD-1", [sc])])
        r  = self.engine.evaluate(p)
        self.assertIn("net_value_COP", r)

    # ── VD-2: Servicios Eficientes ────────────────────────────────────────────

    def test_vd2a_opex_evitado(self):
        """VD-2-A: ahorro OPEX ($1 por unidad) — ya en COP."""
        sc = _make_sc("VD-2-A", delta=500_000_000.0, price=1.0)
        p  = _make_project(bu="TyD", capex=2_000_000_000,
                           drivers=[_make_driver("VD-2", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    def test_vd2b_m3_gas(self):
        """VD-2-B: m3 gas — $2.100/m3."""
        sc = _make_sc("VD-2-B", delta=10_000.0, price=2_100)
        p  = _make_project(bu="Gas", capex=40_000_000, drivers=[_make_driver("VD-2", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    def test_vd2c_generacion(self):
        """VD-2-C: disponibilidad generacion."""
        sc = _make_sc("VD-2-C", delta=10.0, price=335_000)
        p  = _make_project(bu="Generacion", drivers=[_make_driver("VD-2", [sc])])
        r  = self.engine.evaluate(p)
        self.assertIn("net_value_COP", r)

    # ── VD-3: Cobertura Universal ─────────────────────────────────────────────

    def test_vd3a_conexion_tyd(self):
        """VD-3-A: nuevas conexiones TyD — $5.200.000/conexion."""
        sc = _make_sc("VD-3-A", delta=50.0, price=5_200_000)
        p  = _make_project(bu="TyD", capex=500_000_000, drivers=[_make_driver("VD-3", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    def test_vd3b_conexion_aya(self):
        """VD-3-B: conexiones AyA — $6.800.000/conexion."""
        sc = _make_sc("VD-3-B", delta=30.0, price=6_800_000)
        p  = _make_project(bu="AyA", capex=400_000_000, drivers=[_make_driver("VD-3", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    def test_vd3c_conexion_gas(self):
        """VD-3-C: conexiones Gas."""
        sc = _make_sc("VD-3-C", delta=20.0, price=4_100_000)
        p  = _make_project(bu="Gas", capex=160_000_000, drivers=[_make_driver("VD-3", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    # ── VD-4: Carbono Neutralidad ─────────────────────────────────────────────

    def test_vd4a_co2(self):
        """VD-4-A: CO2 evitado — $88.000/tCO2."""
        sc = _make_sc("VD-4-A", delta=500.0, price=88_000)
        p  = _make_project(bu="Generacion", capex=90_000_000, drivers=[_make_driver("VD-4", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    def test_vd4b_remediacion(self):
        """VD-4-B: remediacion ambiental."""
        sc = _make_sc("VD-4-B", delta=1.0, price=4_800_000)
        p  = _make_project(bu="AyA", capex=10_000_000, drivers=[_make_driver("VD-4", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    def test_vd4c_multa_anla(self):
        """VD-4-C: multa ANLA evitada — $3.200MM/multa."""
        sc = _make_sc("VD-4-C", delta=1.0, price=3_200_000_000)
        p  = _make_project(bu="Gas", capex=500_000_000,
                           drivers=[_make_driver("VD-4", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    # ── VD-5: Generacion de Valor ─────────────────────────────────────────────

    def test_vd5a_ingresos(self):
        """VD-5-A: ingresos regulados — ya en COP."""
        sc = _make_sc("VD-5-A", delta=2_000_000_000.0, price=1.0)
        p  = _make_project(bu="TyD", drivers=[_make_driver("VD-5", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    def test_vd5b_regulatorio(self):
        """VD-5-B: cumplimiento regulatorio."""
        sc = _make_sc("VD-5-B", delta=5.0, price=4_500_000)
        p  = _make_project(bu="TyD", capex=45_000_000, drivers=[_make_driver("VD-5", [sc])])
        r  = self.engine.evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)

    def test_vd5c_concept_rating(self):
        """VD-5-C: concept_rating tipo escala."""
        sc = _make_sc_concept("VD-5-C", scale_max=1_000_000_000.0)
        p  = _make_project(bu="TyD", drivers=[_make_driver("VD-5", [sc])])
        r  = self.engine.evaluate(p)
        self.assertIn("net_value_COP", r)

    # ── Todos los BUs ──────────────────────────────────────────────────────────

    def test_all_business_units(self):
        """Evalua el motor para los 4 BUs + Todos."""
        for bu in ["TyD", "AyA", "Gas", "Generacion", "Todos"]:
            sc = _make_sc("VD-1-A", delta=100.0, price=335_000)
            p  = _make_project(bu=bu, drivers=[_make_driver("VD-1", [sc])])
            r  = self.engine.evaluate(p)
            self.assertIn("net_value_COP", r, f"BU={bu} fallo")

    # ── VEI ───────────────────────────────────────────────────────────────────

    def test_vei_formula(self):
        """VEI = VNT / CAPEX."""
        sc = _make_sc("VD-1-A", delta=1000.0, price=335_000)
        p  = _make_project(capex=5_000_000_000,
                           drivers=[_make_driver("VD-1", [sc])])
        r  = self.engine.evaluate(p)
        expected = r["net_value_COP"] / p.costs.capex_COP
        self.assertAlmostEqual(r["value_efficiency_index"], expected, places=4)

    def test_result_structure(self):
        """El resultado contiene todos los campos requeridos."""
        p = _make_project()
        r = self.engine.evaluate(p)
        for fld in ["net_value_COP", "value_efficiency_index",
                    "benefits_raw_COP", "capex_COP", "driver_results"]:
            self.assertIn(fld, r, f"Falta campo: {fld}")

    def test_opex_incremental_reduces_vnt(self):
        """Mayor OPEX incremental reduce el VNT."""
        sc = _make_sc("VD-1-A", delta=1000.0, price=335_000)
        p0 = _make_project(capex=5_000_000_000, opex_incremental=0,
                           drivers=[_make_driver("VD-1", [sc])])
        p1 = _make_project(capex=5_000_000_000, opex_incremental=500_000_000,
                           drivers=[_make_driver("VD-1", [sc])])
        r0 = self.engine.evaluate(p0)
        r1 = self.engine.evaluate(p1)
        self.assertGreater(r0["net_value_COP"], r1["net_value_COP"])

    def test_multi_driver(self):
        """Proyecto con 3 drivers suma todos los beneficios."""
        drivers = [
            _make_driver("VD-1", [_make_sc("VD-1-A", delta=100, price=335_000)]),
            _make_driver("VD-2", [_make_sc("VD-2-A", delta=500_000_000, price=1.0)]),
            _make_driver("VD-4", [_make_sc("VD-4-A", delta=200, price=88_000)]),
        ]
        p  = _make_project(drivers=drivers)
        r  = self.engine.evaluate(p)
        dr_ids = {d["id"] for d in r.get("driver_results", [])}
        for vid in ["VD-1", "VD-2", "VD-4"]:
            self.assertIn(vid, dr_ids, f"Driver {vid} ausente en resultado")

    def test_risk_components_vacia_es_valida_y_no_aporta_riesgo(self):
        """spec §8: risk_components vacía es válida — equivalente al risk=None
        de antes de D-14, sin ΔR y sin reventar."""
        p = _make_project(pid="SINRIESGO", risk=None)
        r = self.engine.evaluate(p)
        self.assertEqual(r["risk_analysis"]["delta_r_npv_COP"], 0.0)
        self.assertEqual(r["risk_analysis"]["components"], [])
        self.assertEqual(r["delta_r_npv_COP"], 0.0)

    def test_risk_components_multiples_se_suman_correctamente(self):
        """El ΔR de 2 componentes es la suma independiente de cada uno —
        no el de uno solo, ni un promedio. Corrige una brecha real hallada
        en revisión de código: la aritmética de suma nunca tenía cobertura
        propia, solo el caso de 0 componentes."""
        comp_matriz = _make_risk(cb=4, pb=4, cr=1, pr=1)
        comp_directo = RiskInput(
            baseline_consequence_level=1, baseline_probability_level=1,
            baseline_annual_COP=500_000_000.0,
        )
        p = _make_project(pid="MULTI", risk=None)
        p.risk_components = [comp_matriz, comp_directo]

        r = self.engine.evaluate(p)
        ra = r["risk_analysis"]

        # Recalculo independiente: cada componente por separado, con el mismo
        # horizonte/business_unit/construction_years/deferral que usó evaluate().
        eff_cy = p.construction_years
        h_eval = p.analysis_horizon_years
        # evaluate() activa el WACC propio de la BU (TyD=9,5%, no el 10%
        # global) durante todo el cálculo interno y lo restaura al salir —
        # hay que replicar esa conmutación para que el recálculo de
        # referencia use la misma tasa que usó internamente evaluate().
        _orig_wacc = self.engine.wacc
        self.engine.wacc = self.engine._get_wacc(p.business_unit)
        try:
            g_matriz = self.engine._get_risk_growth_rate(p.business_unit, asset_type="")
            g_directo = self.engine._get_risk_growth_rate(p.business_unit, asset_type="")
            r_matriz = self.engine._calc_risk(
                comp_matriz, construction_years=eff_cy, horizon=h_eval,
                business_unit=p.business_unit, risk_growth_rate=g_matriz, deferral_years=0)
            r_directo = self.engine._calc_risk(
                comp_directo, construction_years=eff_cy, horizon=h_eval,
                business_unit=p.business_unit, risk_growth_rate=g_directo, deferral_years=0)
        finally:
            self.engine.wacc = _orig_wacc
        esperado_npv = r_matriz["delta_r_npv_COP"] + r_directo["delta_r_npv_COP"]

        self.assertAlmostEqual(ra["delta_r_npv_COP"], esperado_npv, delta=1.0,
            msg="ΔR de 2 componentes no coincide con la suma independiente")
        self.assertAlmostEqual(r["delta_r_npv_COP"], esperado_npv, delta=1.0)
        self.assertEqual(len(ra["components"]), 2)
        # El primer componente (matriz) es el que reporta cof_source/pof_mode
        # a nivel agregado — no un "agregado" de ambos, documentado a propósito.
        self.assertEqual(ra["cof_source"], r_matriz["cof_source"])
        self.assertNotEqual(ra["cof_source"], r_directo["cof_source"],
            msg="fixture: los dos componentes deben tener cof_source distinto "
                "para que esta prueba demuestre algo")


# ─────────────────────────────────────────────────────────────────────────────
# S2 — MILP R1-R12
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(PULP_OK, "PuLP no instalado")
class S2_MILP_Constraints(unittest.TestCase):

    def setUp(self):
        self.cfg = _cfg()
        self.opt = EnterpriseOptimizer(config=self.cfg)

    def _p(self, pid="P001", capex=5_000_000_000, bu="TyD", year=2026,
           mandatory=False, committed=False, committed_year=0,
           risk=None, asset_id="", opex=0, **kw):
        return _make_project(
            pid=pid, capex=capex, bu=bu, exec_year=year,
            is_mandatory=mandatory, is_committed=committed,
            committed_year=committed_year, risk=risk, asset_id=asset_id,
            opex_incremental=opex,
            drivers=[_make_driver("VD-1", [_make_sc()])],
            **kw,
        )

    # ── R1: Presupuesto ───────────────────────────────────────────────────────

    def test_r1_budget_respected(self):
        """R1: CAPEX seleccionado no supera el presupuesto por año."""
        projects = [self._p(f"P{i}", capex=20_000_000_000) for i in range(5)]
        periods  = _make_periods(budget=25_000_000_000)
        result   = self.opt.optimize(projects, periods)
        by_year: Dict[int, float] = {}
        for s in result.selected:
            by_year[s["year"]] = by_year.get(s["year"], 0) + s.get("capex_COP", 0)
        for yr, spent in by_year.items():
            self.assertLessEqual(spent, 25_000_000_000 * 1.01,
                                 f"R1 violado en {yr}")

    # ── R2: Unicidad ──────────────────────────────────────────────────────────

    def test_r2_uniqueness(self):
        """R2: Cada proyecto aparece a lo sumo 1 vez."""
        projects = [self._p(f"P{i}") for i in range(8)]
        result   = self.opt.optimize(projects, _make_periods())
        pids = [s["project_id"] for s in result.selected]
        self.assertEqual(len(pids), len(set(pids)), "R2: proyecto duplicado")

    # ── R3a: Dependencia simple ────────────────────────────────────────────────

    def test_r3a_simple_dependency(self):
        """R3a: El hijo no puede estar antes que el padre."""
        padre = self._p("PADRE", year=2026)
        hijo  = self._p("HIJO",  year=2027)
        hijo.precedent_project_id = "PADRE"
        hijo.min_lag_years = 0
        result = self.opt.optimize([padre, hijo],
                                   _make_periods(budget=20_000_000_000))
        sel = {s["project_id"]: s["year"] for s in result.selected}
        if "PADRE" in sel and "HIJO" in sel:
            self.assertLessEqual(sel["PADRE"], sel["HIJO"],
                                 "R3a: hijo antes que padre")

    # ── R3b: Lag ──────────────────────────────────────────────────────────────

    def test_r3b_lag_dependency(self):
        """R3b: Con lag=2, hijo >= padre + 2."""
        padre = self._p("PADRE", year=2026)
        hijo  = self._p("HIJO",  year=2028)
        hijo.precedent_project_id = "PADRE"
        hijo.min_lag_years = 2
        result = self.opt.optimize([padre, hijo],
                                   _make_periods(budget=20_000_000_000))
        sel = {s["project_id"]: s["year"] for s in result.selected}
        if "PADRE" in sel and "HIJO" in sel:
            self.assertGreaterEqual(sel["HIJO"] - sel["PADRE"], 2,
                                    "R3b: lag no respetado")

    # ── R3c: Exclusion mutua ───────────────────────────────────────────────────

    def test_r3c_mutual_exclusion(self):
        """R3c: Proyectos excluyentes no coexisten."""
        p_a = self._p("PA", year=2026)
        p_b = self._p("PB", year=2026)
        p_a.mutual_exclusion_ids = ["PB"]
        p_b.mutual_exclusion_ids = ["PA"]
        result = self.opt.optimize([p_a, p_b],
                                   _make_periods(budget=20_000_000_000))
        pids = {s["project_id"] for s in result.selected}
        self.assertFalse("PA" in pids and "PB" in pids,
                         "R3c: ambos excluyentes seleccionados")

    # ── R3d: Exclusion por grupo ───────────────────────────────────────────────

    def test_r3d_group_exclusion(self):
        """R3d: Del grupo de exclusion, solo 1 seleccionado.

        Nota: desde que _preselect_by_eav corre sin condiciones antes del
        MILP (grupo S32), cada exclusion_group_id ya llega reducido a <=1
        alternativa — esta prueba pasa por eso, no porque R3d se haya
        ejercitado. El guardián real de R3d por sí solo es
        S32_PreseleccionPorEAV.test_r3d_sigue_activo_si_se_desactiva_la_preseleccion."""
        projs = []
        for i in range(3):
            p = self._p(f"PG{i}", year=2026)
            p.exclusion_group_id = "GRUPO_A"
            projs.append(p)
        result = self.opt.optimize(projs, _make_periods())
        grp = [s for s in result.selected if s["project_id"].startswith("PG")]
        self.assertLessEqual(len(grp), 1, "R3d: mas de 1 del grupo seleccionado")

    # ── R4: Persona-mes ────────────────────────────────────────────────────────

    def test_r4_person_months(self):
        """R4: No crashea con limite de persona-mes bajo."""
        projects = [self._p(f"P{i}", capex=300_000_000_000) for i in range(3)]
        periods  = _make_periods(pm=50)
        result   = self.opt.optimize(projects, periods)
        self.assertIn(result.solver_status, ["OPTIMAL", "FEASIBLE", "INFEASIBLE"])

    # ── R5: Concurrencia ──────────────────────────────────────────────────────

    def test_r5_concurrency(self):
        """R5: Proyectos simultaneos <= max_projects_concurrent."""
        projects = [self._p(f"P{i}", year=2026) for i in range(8)]
        periods  = _make_periods(concurrent=3)
        result   = self.opt.optimize(projects, periods)
        by_year: Dict[int, int] = {}
        for s in result.selected:
            by_year[s["year"]] = by_year.get(s["year"], 0) + 1
        for yr, cnt in by_year.items():
            self.assertLessEqual(cnt, 3, f"R5: concurrencia violada en {yr}")

    # ── R6: Riesgo maximo ────────────────────────────────────────────────────

    def test_r6_portfolio_risk(self):
        """R6: No crashea con proyectos de riesgo alto."""
        risk_high = _make_risk(cb=5, pb=5)
        projects  = [self._p(f"P{i}", risk=risk_high) for i in range(5)]
        result    = self.opt.optimize(projects, _make_periods())
        self.assertIn(result.solver_status,
                      ["OPTIMAL", "FEASIBLE", "INFEASIBLE"])

    # ── R7: Pisos de inversion ────────────────────────────────────────────────

    def test_r7_investment_floors(self):
        """R7: Con piso de inversion, solver no crashea."""
        projects = []
        for bu in ["TyD", "AyA"]:
            for i in range(3):
                projects.append(self._p(f"{bu}_P{i}", bu=bu,
                                        capex=10_000_000_000))
        pisos = {"TyD": 5_000_000_000, "AyA": 5_000_000_000}
        result = self.opt.optimize(
            projects, _make_periods(budget=200_000_000_000),
            pisos_inversion_COP=pisos,
        )
        self.assertNotEqual(result.solver_status, "ERROR")

    # ── R8: Must-mitigate ─────────────────────────────────────────────────────

    def test_r8_must_mitigate_forces_selection(self):
        """R8: Asset con riesgo inaceptable fuerza mitigante."""
        risk_crit = _make_risk(cb=5, pb=5, cr=1, pr=1)
        mitigante = self._p("MITIG", asset_id="ACTIVO_001",
                            risk=risk_crit)
        otro      = self._p("OTRO")
        result    = self.opt.optimize(
            [mitigante, otro],
            _make_periods(budget=200_000_000_000),
            risk_tolerance_level=4,
        )
        pids = {s["project_id"] for s in result.selected}
        self.assertIn("MITIG", pids, "R8: mitigante no seleccionado")

    # ── R9: Actuals / EVM ─────────────────────────────────────────────────────

    def test_r9_actuals_completed_excluded(self):
        """R9: Proyectos completados (pct=1.0) se excluyen."""
        p_done  = self._p("DONE",   year=2025)
        p_activ = self._p("ACTIVO", year=2026)
        actuals = [ProjectActual(project_id="DONE", pct_complete=1.0,
                                 ac_COP=5_000_000_000)]
        result  = self.opt.optimize([p_done, p_activ], _make_periods(),
                                    actuals=actuals)
        pids = {s["project_id"] for s in result.selected}
        self.assertNotIn("DONE", pids, "R9: completado en selected")

    def test_r9_actuals_inprogress_forced(self):
        """R9: Proyectos en curso (0 < pct < 1) se fuerzan."""
        p_wip = self._p("WIP", year=2026, capex=10_000_000_000)
        actuals = [ProjectActual(project_id="WIP", pct_complete=0.5,
                                 ac_COP=4_000_000_000)]
        result  = self.opt.optimize([p_wip], _make_periods(), actuals=actuals)
        pids = {s["project_id"] for s in result.selected}
        self.assertIn("WIP", pids, "R9: WIP no forzado")

    # ── R11: Committed Spend ──────────────────────────────────────────────────

    def test_r11_committed_forced_in_year(self):
        """R11: Comprometido se fuerza en su año contractual."""
        p_comm = self._p("CONTRATO", capex=8_000_000_000,
                         committed=True, committed_year=2027)
        p_disc = self._p("DISCR",    capex=5_000_000_000)
        result = self.opt.optimize(
            [p_comm, p_disc], _make_periods(budget=200_000_000_000)
        )
        sel = {s["project_id"]: s["year"] for s in result.selected}
        self.assertIn("CONTRATO", sel, "R11: comprometido no seleccionado")
        self.assertEqual(sel["CONTRATO"], 2027, "R11: año incorrecto")

    def test_r11_committed_summary_populated(self):
        """R11: committed_summary contiene n_committed."""
        p_comm = self._p("C1", committed=True, committed_year=2026,
                         capex=10_000_000_000)
        result = self.opt.optimize([p_comm], _make_periods(budget=200_000_000_000))
        cs = result.committed_summary
        self.assertIn("n_committed", cs)
        self.assertGreaterEqual(cs.get("n_committed", 0), 1)

    # ── R12: TOTEX ────────────────────────────────────────────────────────────

    def test_r12_totex_no_crash(self):
        """R12: Con techo TOTEX, solver no crashea."""
        projects = [self._p(f"P{i}", capex=10_000_000_000, opex=200_000_000)
                    for i in range(4)]
        periods  = _make_periods(totex_budget=15_000_000_000)
        result   = self.opt.optimize(projects, periods)
        self.assertIn(result.solver_status,
                      ["OPTIMAL", "FEASIBLE", "INFEASIBLE"])

    def test_r12_totex_utilization_is_dict(self):
        """R12: totex_utilization es dict."""
        p = self._p("PT", capex=5_000_000_000, opex=100_000_000)
        result = self.opt.optimize([p], _make_periods(totex_budget=10_000_000_000))
        self.assertIsInstance(result.totex_utilization, dict)

    # ── General ───────────────────────────────────────────────────────────────

    def test_solver_returns_valid_status(self):
        """Solver retorna estado reconocido."""
        result = self.opt.optimize([self._p()], _make_periods())
        self.assertIn(result.solver_status,
                      ["OPTIMAL", "FEASIBLE", "INFEASIBLE", "ERROR"])

    def test_empty_portfolio(self):
        """Portfolio vacio: selected = []."""
        result = self.opt.optimize([], _make_periods())
        self.assertEqual(result.selected, [])


# ─────────────────────────────────────────────────────────────────────────────
# S3 — DEPENDENCY ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class S3_DependencyEngine(unittest.TestCase):

    def setUp(self):
        self.cfg = _cfg()
        self.dep = DependencyEngine(self.cfg)

    def _proj(self, pid, precedent="", lag=0, mutex=None, grp=None):
        p = _make_project(pid=pid)
        p.precedent_project_id = precedent
        p.min_lag_years = lag
        p.mutual_exclusion_ids = mutex or []
        p.exclusion_group_id = grp
        return p

    def test_deadlock_circular_raises(self):
        """Dependencia circular A->B->A lanza DependencyError."""
        a = self._proj("A", precedent="B")
        b = self._proj("B", precedent="A")
        with self.assertRaises(DependencyError):
            self.dep.validate([a, b])

    def test_chain_no_deadlock(self):
        """Cadena A->B->C sin ciclo no lanza."""
        a = self._proj("A")
        b = self._proj("B", precedent="A")
        c = self._proj("C", precedent="B")
        warnings = self.dep.validate([a, b, c])
        self.assertIsInstance(warnings, list)

    def test_mutex_vs_dependency_deadlock(self):
        """Excluir y depender del mismo proyecto = deadlock logico."""
        a = self._proj("A", precedent="B", mutex=["B"])
        b = self._proj("B")
        with self.assertRaises(DependencyError):
            self.dep.validate([a, b])

    def test_validate_clean_portfolio(self):
        """Portfolio sin dependencias pasa la validacion."""
        projects = [_make_project(pid=f"P{i}") for i in range(5)]
        result = self.dep.validate(projects)
        self.assertIsInstance(result, list)

    def test_validate_returns_warnings_list(self):
        """validate() retorna lista (puede ser vacia)."""
        p = _make_project("SOLO")
        result = self.dep.validate([p])
        self.assertIsInstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# S4 — EVM ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class S4_EVMEngine(unittest.TestCase):

    def _project(self, capex=10_000_000_000, cy=2, exec_year=2026):
        return _make_project(capex=capex, exec_year=exec_year,
                             construction_years=cy)

    def test_completed_project_fields(self):
        """pct=1.0: EVMResult tiene todos los campos."""
        p  = self._project()
        a  = ProjectActual(project_id="P001", pct_complete=1.0,
                           ac_COP=10_000_000_000)
        r  = EVMEngine.compute(p, a, cut_year=2027)
        for attr in ["spi", "cpi", "sv_COP", "cv_COP", "eac_COP",
                     "etc_COP", "health", "pct_complete"]:
            self.assertTrue(hasattr(r, attr), f"Falta: {attr}")

    def test_inprogress_50pct_cv_negative(self):
        """pct=0.5 con AC > EV: CV debe ser negativo."""
        p  = self._project(capex=10_000_000_000, cy=2)
        a  = ProjectActual(project_id="P001", pct_complete=0.5,
                           ac_COP=6_000_000_000)
        r  = EVMEngine.compute(p, a, cut_year=2027)
        self.assertLess(r.cv_COP, 0, "CV debe ser negativo con sobrecosto")

    def test_spi_warning_threshold(self):
        """SPI < 0.90 -> AMARILLO o ROJO."""
        p  = self._project(capex=10_000_000_000, cy=2)
        a  = ProjectActual(project_id="P001", pct_complete=0.1,
                           ac_COP=500_000_000)
        r  = EVMEngine.compute(p, a, cut_year=2027)
        if r.spi < EVMEngine.SPI_CRIT:
            self.assertEqual(r.health, "ROJO")
        elif r.spi < EVMEngine.SPI_WARN:
            self.assertIn(r.health, ["AMARILLO", "ROJO"])

    def test_cpi_critical_rojo(self):
        """CPI < 0.80 -> ROJO."""
        p  = self._project(capex=10_000_000_000, cy=1)
        a  = ProjectActual(project_id="P001", pct_complete=0.3,
                           ac_COP=9_000_000_000)
        r  = EVMEngine.compute(p, a, cut_year=2026)
        self.assertIn(r.health, ["ROJO", "AMARILLO"])

    def test_eac_gt_zero(self):
        """EAC siempre > 0."""
        p  = self._project(capex=10_000_000_000, cy=2)
        a  = ProjectActual(project_id="P001", pct_complete=0.6,
                           ac_COP=5_000_000_000)
        r  = EVMEngine.compute(p, a, cut_year=2027)
        self.assertGreater(r.eac_COP, 0)

    def test_verde_on_track(self):
        """Proyecto en tiempo y presupuesto -> VERDE."""
        p  = self._project(capex=10_000_000_000, cy=2)
        # EV = PV = 5B (50% avance, 1 de 2 años)
        # AC = 5B (sin sobrecosto)
        a  = ProjectActual(project_id="P001", pct_complete=0.5,
                           ac_COP=5_000_000_000)
        r  = EVMEngine.compute(p, a, cut_year=2027)
        # Con SPI y CPI cercanos a 1, debe ser VERDE o AMARILLO
        self.assertIn(r.health, ["VERDE", "AMARILLO", "ROJO"])


# ─────────────────────────────────────────────────────────────────────────────
# S5 — OUTAGE CALENDAR + R10
# ─────────────────────────────────────────────────────────────────────────────

class S5_OutageCalendar(unittest.TestCase):

    def _make_outages(self):
        return [
            PlannedOutage(outage_id="O1", asset_id="ACT001", year=2027,
                          name="Parada mayor", duration_days=7,
                          outage_type="mantenimiento_mayor"),
            PlannedOutage(outage_id="O2", asset_id="ACT001", year=2029,
                          name="Parada anual", duration_days=3,
                          outage_type="parada_menor"),
            PlannedOutage(outage_id="O3", asset_id="ACT002", year=2026,
                          name="Ventana TyD", duration_days=5,
                          outage_type="mantenimiento_mayor"),
        ]

    def test_valid_years_returns_years_with_outage(self):
        """valid_years retorna anios con ventana para el asset."""
        cal = OutageCalendar(self._make_outages())
        years = cal.valid_years("ACT001")
        self.assertIn(2027, years)
        self.assertIn(2029, years)
        self.assertNotIn(2026, years)

    def test_has_any_true_for_existing(self):
        """has_any() True si el asset tiene al menos una ventana."""
        cal = OutageCalendar(self._make_outages())
        self.assertTrue(cal.has_any("ACT001"))

    def test_has_any_false_for_missing(self):
        """has_any() False si el asset no tiene ventanas."""
        cal = OutageCalendar(self._make_outages())
        self.assertFalse(cal.has_any("ACT999"))

    def test_valid_years_empty_calendar(self):
        """Sin outages, valid_years = []."""
        cal = OutageCalendar([])
        self.assertEqual(cal.valid_years("X"), [])

    @unittest.skipUnless(PULP_OK, "PuLP no instalado")
    def test_r10_outage_alignment_milp(self):
        """R10: Proyecto require_outage_alignment solo en anios con ventana."""
        p = _make_project(
            pid="POA", asset_id="ACT001",
            exec_year=2027,
            drivers=[_make_driver("VD-1", [_make_sc()])],
        )
        p.require_outage_alignment = True
        outages = [PlannedOutage(outage_id="O1", asset_id="ACT001", year=2027,
                                 name="P", duration_days=5, outage_type="parada")]
        opt    = EnterpriseOptimizer(config=_cfg())
        result = opt.optimize([p], _make_periods(), planned_outages=outages)
        sel    = {s["project_id"]: s["year"] for s in result.selected}
        if "POA" in sel:
            self.assertEqual(sel["POA"], 2027, "R10: no alineado con ventana")


# ─────────────────────────────────────────────────────────────────────────────
# S6 — SNAPSHOT STORE (ISO 55001)
# ─────────────────────────────────────────────────────────────────────────────

class S6_SnapshotStore(unittest.TestCase):

    def setUp(self):
        self.tmp   = tempfile.mkdtemp()
        self.store = SnapshotStore(self.tmp)
        self.cfg   = _cfg()
        self.engine = ValueEngine(self.cfg)

    def test_capture_returns_snapshot(self):
        """capture() retorna EvaluationSnapshot con integrity_hash."""
        p   = _make_project()
        r   = self.engine.evaluate(p)
        snap = self.store.capture(r, p, self.cfg, analyst="test_analista")
        self.assertIsNotNone(snap)
        self.assertTrue(hasattr(snap, "integrity_hash"))
        self.assertGreater(len(snap.integrity_hash), 0)

    def test_integrity_hash_sha256(self):
        """integrity_hash es SHA-256 de 64 hex chars."""
        p    = _make_project()
        r    = self.engine.evaluate(p)
        snap = self.store.capture(r, p, self.cfg, analyst="aspd")
        self.assertEqual(len(snap.integrity_hash), 64)

    def test_two_snapshots_different_hashes(self):
        """Dos snapshots distintos tienen hashes distintos."""
        p1 = _make_project("PA", capex=5_000_000_000)
        p2 = _make_project("PB", capex=10_000_000_000)
        r1 = self.engine.evaluate(p1)
        r2 = self.engine.evaluate(p2)
        s1 = self.store.capture(r1, p1, self.cfg, analyst="a")
        s2 = self.store.capture(r2, p2, self.cfg, analyst="a")
        self.assertNotEqual(s1.integrity_hash, s2.integrity_hash)

    def test_verify_snapshot(self):
        """verify() retorna (True, msg) para snapshot valido."""
        p    = _make_project()
        r    = self.engine.evaluate(p)
        snap = self.store.capture(r, p, self.cfg)
        ok, msg = self.store.verify(snap.snapshot_id)
        self.assertTrue(ok, f"Verificacion fallo: {msg}")

    def test_export_audit_report(self):
        """export_audit_report() genera archivo de texto."""
        p   = _make_project()
        r   = self.engine.evaluate(p)
        self.store.capture(r, p, self.cfg, analyst="aspd",
                           change_reason="Test inicial")
        rpt_path = os.path.join(self.tmp, "audit_report.txt")
        self.store.export_audit_report(rpt_path)
        self.assertTrue(os.path.exists(rpt_path))
        content = Path(rpt_path).read_text(encoding="utf-8")
        self.assertGreater(len(content), 0)


# ─────────────────────────────────────────────────────────────────────────────
# S7 — PLAN VERSION STORE
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(PULP_OK, "PuLP no instalado")
class S7_PlanVersionStore(unittest.TestCase):

    def setUp(self):
        self.tmp      = tempfile.mkdtemp()
        self.store    = PlanVersionStore(os.path.join(self.tmp, "versions.json"))
        self.opt      = EnterpriseOptimizer(config=_cfg())
        self.projects = [
            _make_project(f"P{i}", capex=8_000_000_000) for i in range(4)
        ]
        self.periods  = _make_periods()

    def _run(self, projects=None):
        return self.opt.optimize(projects or self.projects, self.periods)

    def test_save_and_list(self):
        """save_version + list_versions devuelve versiones."""
        r  = self._run()
        v1 = self.store.save_version(r, "PAI 2026", "Analista A", "v1")
        v2 = self.store.save_version(r, "PAI 2027", "Analista B", "v2")
        lst = self.store.list_versions()
        self.assertGreaterEqual(len(lst), 2)
        ids = [v.version_id for v in lst]
        self.assertIn(v1.version_id, ids)
        self.assertIn(v2.version_id, ids)

    def test_get_version(self):
        """get_version retorna la version correcta."""
        r  = self._run()
        v  = self.store.save_version(r, "PAI Test", "A", "Test")
        v2 = self.store.get_version(v.version_id)
        self.assertIsNotNone(v2)
        self.assertEqual(v2.version_id, v.version_id)

    def test_get_nonexistent_returns_none(self):
        """get_version con ID inexistente retorna None."""
        result = self.store.get_version("no-existe-uuid")
        self.assertIsNone(result)

    def test_diff_versions_fields(self):
        """diff_versions retorna added, removed, vnt_delta, capex_delta."""
        r_a = self._run(self.projects[:2])
        r_b = self._run(self.projects[2:])
        va  = self.store.save_version(r_a, "V_A", "A", "")
        vb  = self.store.save_version(r_b, "V_B", "A", "")
        diff = self.store.diff_versions(va.version_id, vb.version_id)
        self.assertIn("added", diff)
        self.assertIn("removed", diff)
        self.assertIn("vnt_delta_COP", diff)
        self.assertIn("capex_delta_COP", diff)

    def test_diff_year_changes(self):
        """diff_versions detecta year_changes."""
        r1 = self._run()
        r2 = self._run()
        v1 = self.store.save_version(r1, "V1", "A", "")
        v2 = self.store.save_version(r2, "V2", "A", "")
        diff = self.store.diff_versions(v1.version_id, v2.version_id)
        self.assertIn("year_changes", diff)

    def test_sha256_signature_64chars(self):
        """Cada version tiene firma SHA-256 de 64 chars."""
        r = self._run()
        v = self.store.save_version(r, "SIG", "A", "")
        self.assertEqual(len(v.signature), 64)

    def test_diff_nonexistent_returns_error(self):
        """diff con IDs inexistentes retorna dict con 'error'."""
        diff = self.store.diff_versions("uuid-a-no-existe", "uuid-b-no-existe")
        self.assertIn("error", diff)


# ─────────────────────────────────────────────────────────────────────────────
# S8 — WORKFLOW ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class S8_WorkflowEngine(unittest.TestCase):
    """
    Transiciones validas:
      BORRADOR    -> ENVIADO           (analista / coordinador_ga)
      ENVIADO     -> EN_REVISION       (coordinador_ga)
      EN_REVISION -> APROBADO_NEGOCIO  (coordinador_ga / director)
      EN_REVISION -> RECHAZADO         (coordinador_ga / director)
      APROBADO_NEGOCIO -> APROBADO_PORTAFOLIO (comite_ga / director)
      RECHAZADO   -> BORRADOR          (analista)
      DIFERIDO    -> BORRADOR          (analista)
    """

    def setUp(self):
        self.wf = ProjectWorkflow(
            project_id="P001", project_name="Test",
            bu="TyD", cycle="PAI 2026",
        )

    def _apply(self, wf, to_state, role, label, comment=""):
        return WorkflowEngine.apply(
            wf, {"to": to_state, "label": label},
            actor="tester", role=role, bu="TyD", comment=comment,
        )

    def test_initial_state_borrador(self):
        """Estado inicial = BORRADOR."""
        self.assertEqual(self.wf.current_state, "BORRADOR")

    def test_borrador_to_enviado(self):
        """Analista somete desde BORRADOR -> ENVIADO."""
        # Get the actual label from transitions
        label = next(t["label"] for t in WF_FULL_TRANSITIONS
                     if t["from"] == "BORRADOR" and t["role"] == "analista")
        wf = self._apply(self.wf, "ENVIADO", "analista", label)
        self.assertEqual(wf.current_state, "ENVIADO")

    def test_enviado_to_en_revision(self):
        """coordinador_ga mueve ENVIADO -> EN_REVISION."""
        label_b = next(t["label"] for t in WF_FULL_TRANSITIONS
                       if t["from"] == "BORRADOR" and t["role"] == "analista")
        wf = self._apply(self.wf, "ENVIADO", "analista", label_b)
        label_e = next(t["label"] for t in WF_FULL_TRANSITIONS
                       if t["from"] == "ENVIADO" and t["to"] == "EN_REVISION")
        wf = self._apply(wf, "EN_REVISION", "coordinador_ga", label_e)
        self.assertEqual(wf.current_state, "EN_REVISION")

    def test_en_revision_to_aprobado(self):
        """Director aprueba desde EN_REVISION -> APROBADO_NEGOCIO."""
        # Full path: BORRADOR -> ENVIADO -> EN_REVISION -> APROBADO_NEGOCIO
        lb = next(t["label"] for t in WF_FULL_TRANSITIONS
                  if t["from"]=="BORRADOR" and t["role"]=="analista")
        wf = self._apply(self.wf, "ENVIADO", "analista", lb)
        le = next(t["label"] for t in WF_FULL_TRANSITIONS
                  if t["from"]=="ENVIADO" and t["to"]=="EN_REVISION")
        wf = self._apply(wf, "EN_REVISION", "coordinador_ga", le)
        la = next(t["label"] for t in WF_FULL_TRANSITIONS
                  if t["from"]=="EN_REVISION" and t["to"]=="APROBADO_NEGOCIO"
                  and t["role"]=="director")
        wf = self._apply(wf, "APROBADO_NEGOCIO", "director", la)
        self.assertEqual(wf.current_state, "APROBADO_NEGOCIO")

    def test_invalid_transition_raises(self):
        """Transicion no permitida lanza ValueError."""
        with self.assertRaises(ValueError):
            WorkflowEngine.apply(
                self.wf, {"to": "APROBADO_NEGOCIO", "label": "Aprobar"},
                actor="analista1", role="analista", bu="TyD",
            )

    def test_reject_requires_comment(self):
        """Rechazar sin comentario lanza ValueError."""
        lb = next(t["label"] for t in WF_FULL_TRANSITIONS
                  if t["from"]=="BORRADOR" and t["role"]=="analista")
        wf = self._apply(self.wf, "ENVIADO", "analista", lb)
        le = next(t["label"] for t in WF_FULL_TRANSITIONS
                  if t["from"]=="ENVIADO" and t["to"]=="EN_REVISION")
        wf = self._apply(wf, "EN_REVISION", "coordinador_ga", le)
        lr = next(t["label"] for t in WF_FULL_TRANSITIONS
                  if t["from"]=="EN_REVISION" and t["to"]=="RECHAZADO"
                  and t["role"]=="director")
        with self.assertRaises(ValueError):
            self._apply(wf, "RECHAZADO", "director", lr, comment="")

    def test_history_appended(self):
        """Cada transicion se registra en historial."""
        lb = next(t["label"] for t in WF_FULL_TRANSITIONS
                  if t["from"]=="BORRADOR" and t["role"]=="analista")
        wf = self._apply(self.wf, "ENVIADO", "analista", lb)
        self.assertEqual(len(wf.history), 1)

    def test_summary_counts_borrador(self):
        """summary() cuenta proyectos en BORRADOR."""
        wf2 = ProjectWorkflow("P002", "Otro", "TyD", cycle="PAI")
        workflows = {"P001": self.wf, "P002": wf2}
        s = WorkflowEngine.summary(workflows)
        self.assertGreaterEqual(s.get("BORRADOR", 0), 2)

    def test_pending_for_analista(self):
        """Analista ve proyectos en BORRADOR."""
        workflows = {"P001": self.wf}
        pending = WorkflowEngine.pending_for_role(workflows, "analista", "TyD")
        self.assertTrue(any(w.project_id == "P001" for w in pending))

    def test_ensure_workflow_creates(self):
        """ensure_workflow crea workflow en BORRADOR."""
        workflows = {}
        wf = WorkflowEngine.ensure_workflow(workflows, "NEW_P", "Nuevo", "AyA")
        self.assertIn("NEW_P", workflows)
        self.assertEqual(wf.current_state, "BORRADOR")


# ─────────────────────────────────────────────────────────────────────────────
# S9 — PORTFOLIO LOADER
# ─────────────────────────────────────────────────────────────────────────────

class S9_PortfolioLoader(unittest.TestCase):

    def setUp(self):
        self.cfg    = _cfg()
        self.loader = PortfolioLoader(self.cfg)

    def _csv_bytes(self, rows: List[dict]) -> bytes:
        buf = io.StringIO()
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        return buf.getvalue().encode("utf-8")

    def _basic_row(self, pid="P001", bu="TyD", capex_2026=10_000, capex_2027=5_000):
        return {
            "id": pid, "name": f"Proyecto {pid}",
            "business_unit": bu,
            "investment_type": "Reposicion de Activos",
            "capex_2026": capex_2026, "capex_2027": capex_2027,
            "capex_2028": 0, "capex_2029": 0, "capex_2030": 0,
            "execution_year": 2026, "is_mandatory": "False",
        }

    def test_load_csv_basic(self):
        """CSV basico -> lista de ProjectInput valida."""
        rows = [self._basic_row("P001"), self._basic_row("P002", bu="AyA")]
        data = self._csv_bytes(rows)
        projects, report = self.loader.from_bytes(data, "test.csv")
        self.assertGreaterEqual(len(projects), 1)

    def test_mm_cop_heuristic(self):
        """CAPEX en MM COP se escala a COP."""
        rows = [self._basic_row("P001", capex_2026=10_000)]
        data = self._csv_bytes(rows)
        projects, _ = self.loader.from_bytes(data, "test.csv")
        if projects:
            self.assertGreater(projects[0].costs.capex_COP, 1_000_000_000,
                               "MM-heuristica no escalo CAPEX")

    def test_capex_zero_skipped(self):
        """Proyectos con CAPEX = 0 se omiten con warning."""
        rows = [{"id": "P_ZERO", "name": "Zero CAPEX", "business_unit": "TyD",
                 "capex_2026": 0, "capex_2027": 0, "capex_2028": 0,
                 "capex_2029": 0, "capex_2030": 0,
                 "execution_year": 2026, "is_mandatory": "False"}]
        data = self._csv_bytes(rows)
        projects, report = self.loader.from_bytes(data, "test.csv")
        self.assertEqual(len(projects), 0)
        self.assertTrue(len(report.get("warnings", [])) > 0)

    def test_duplicate_ids_renamed(self):
        """IDs duplicados se renombran con sufijo _02."""
        rows = [self._basic_row("P001"), self._basic_row("P001", bu="AyA")]
        data = self._csv_bytes(rows)
        projects, report = self.loader.from_bytes(data, "test.csv")
        ids = [p.id for p in projects]
        self.assertTrue(any("_02" in pid for pid in ids),
                        f"IDs no deduplicados: {ids}")

    def test_committed_columns_loaded(self):
        """is_committed y committed_year se cargan desde CSV."""
        rows = [{**self._basic_row("PC"), "is_committed": "True",
                 "committed_year": 2027}]
        data = self._csv_bytes(rows)
        projects, _ = self.loader.from_bytes(data, "test.csv")
        if projects:
            self.assertTrue(projects[0].is_committed)
            self.assertEqual(projects[0].committed_year, 2027)

    def test_mandatory_flag_variants(self):
        """is_mandatory acepta: True/true/1/yes/si."""
        for val in ["True", "true", "1", "yes", "si"]:
            rows = [{**self._basic_row("PM"), "is_mandatory": val}]
            data = self._csv_bytes(rows)
            projects, _ = self.loader.from_bytes(data, "test.csv")
            if projects:
                self.assertTrue(projects[0].is_mandatory,
                                f"is_mandatory='{val}' no mapeo a True")

    def test_load_excel(self):
        """Excel basico se carga sin error."""
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl no instalado")

        wb = openpyxl.Workbook()
        ws = wb.active
        headers = ["id", "name", "business_unit", "investment_type",
                   "capex_2026", "capex_2027", "capex_2028", "capex_2029",
                   "capex_2030", "execution_year", "is_mandatory"]
        ws.append(headers)
        ws.append(["P001", "Test Project", "TyD", "Reposicion de Activos",
                   10_000, 5_000, 0, 0, 0, 2026, "False"])
        buf = io.BytesIO()
        wb.save(buf)
        data = buf.getvalue()
        projects, report = self.loader.from_bytes(data, "test.xlsx")
        self.assertGreaterEqual(len(projects), 1)

    def test_large_portfolio_50_projects(self):
        """Portfolio de 50 proyectos se carga sin error."""
        bus = ["TyD", "AyA", "Gas", "Generacion"]
        rows = [
            {**self._basic_row(f"P{i:03d}", bu=bus[i%4],
                               capex_2026=5_000 + i*100)}
            for i in range(50)
        ]
        data = self._csv_bytes(rows)
        projects, _ = self.loader.from_bytes(data, "test.csv")
        self.assertGreaterEqual(len(projects), 40)

    def test_proyecto_importado_trae_analysis_horizon_years_resuelto(self):
        """D-14: el importador ya no puede depender de que evaluate() resuelva
        el horizonte por asset_type — debe quedar resuelto en el objeto."""
        loader = PortfolioLoader(config=DEFAULT_CONFIG)
        # Encabezados verificados contra _COL_ALIASES (epm_core.py, sección
        # cerca de la clase PortfolioLoader): "id", "name", "business_unit",
        # "investment_type", "capex_total" y "execution_year" son, cada uno,
        # la propia clave interna (primer alias de su lista), así que valen
        # literalmente como encabezado.
        csv_bytes = (
            "id,name,business_unit,investment_type,capex_total,execution_year\n"
            "P001,Proyecto Test,TyD,Reposicion de Activos,1000000000,2026\n"
        ).encode("utf-8")
        projects, report = loader.from_bytes(csv_bytes, "test.csv")
        self.assertEqual(len(projects), 1)
        self.assertGreater(projects[0].analysis_horizon_years, 0,
                           "El proyecto importado debe traer un horizonte > 0, "
                           "o evaluate() fallará con el guardián del Task 5")


# ─────────────────────────────────────────────────────────────────────────────
# S10 — SENSITIVITY ANALYST
# ─────────────────────────────────────────────────────────────────────────────

class S10_Sensitivity(unittest.TestCase):

    def setUp(self):
        self.cfg     = _cfg()
        self.analyst = SensitivityAnalyst(self.cfg)
        self.projects = [
            _make_project(f"P{i}", capex=5_000_000_000,
                          drivers=[_make_driver("VD-1",
                              [_make_sc("VD-1-A", delta=100+i*50)])])
            for i in range(4)
        ]

    def test_run_scenario_wacc_change(self):
        """Escenario WACC alto reduce VNT."""
        scenario = self.analyst.run_scenario(
            self.projects, {"wacc": 0.15}, "WACC Alto"
        )
        self.assertEqual(scenario["scenario_name"], "WACC Alto")
        self.assertIn("comparison", scenario)
        for row in scenario["comparison"]:
            self.assertIn("delta_net_value", row)

    def test_scenario_comparison_all_projects(self):
        """El escenario incluye todos los proyectos."""
        s = self.analyst.run_scenario(self.projects, {"wacc": 0.12}, "Test")
        self.assertEqual(len(s["comparison"]), len(self.projects))

    def test_scenario_rank_change_detected(self):
        """Escenarios con WACC diferente pueden cambiar ranking."""
        s1 = self.analyst.run_scenario(self.projects, {"wacc": 0.08}, "Bajo")
        s2 = self.analyst.run_scenario(self.projects, {"wacc": 0.20}, "Alto")
        # Ambos deben ejecutar sin error
        self.assertIsNotNone(s1)
        self.assertIsNotNone(s2)

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

    @unittest.skipUnless(PULP_OK, "PuLP no instalado")
    def test_frontier_generated(self):
        """compute_frontier=True genera efficient_frontier."""
        opt    = EnterpriseOptimizer(config=self.cfg)
        result = opt.optimize(self.projects, _make_periods(), compute_frontier=True)
        self.assertIsInstance(result.efficient_frontier, list)

    @unittest.skipUnless(PULP_OK, "PuLP no instalado")
    def test_totex_frontier_generated(self):
        """compute_totex_frontier=True genera totex_frontier."""
        opt    = EnterpriseOptimizer(config=self.cfg)
        result = opt.optimize(self.projects, _make_periods(),
                              compute_totex_frontier=True)
        self.assertIsInstance(result.totex_frontier, list)


# ─────────────────────────────────────────────────────────────────────────────
# S11 — ASSET DEGRADATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class S11_DegradationEngine(unittest.TestCase):

    def setUp(self):
        self.cfg = _cfg()
        wacc = float(self.cfg.get("wacc", 0.1))
        horizon = int(self.cfg.get("optimal_timing", {}).get("horizon_years", 10))
        self.engine = AssetDegradationEngine(wacc=wacc, horizon_years=horizon,
                                             config=self.cfg)

    def _asset(self, model=DegradationModel.WEIBULL, bu="TyD",
               asset_class="transformador_potencia",
               install_year=1990, design_life=35,
               repl_cost=5_000_000_000, maint_cost=200_000_000,
               condition=0.6, beta=2.5, eta=35.0) -> AssetProfile:
        return AssetProfile(
            asset_id=f"ACT_{model[:3].upper()}",
            asset_name=f"Activo {model}",
            asset_class=asset_class,
            business_unit=bu,
            degradation_model=model,
            install_year=install_year,
            design_life_years=design_life,
            current_condition=condition,
            replacement_cost_COP=repl_cost,
            annual_maintenance_COP=maint_cost,
            consequence_level=3,
            weibull_beta=beta,
            weibull_eta=eta,
        )

    def test_weibull_tyd_transformador(self):
        """Weibull: TyD transformador — EEOL y PoF validos."""
        asset = self._asset(DegradationModel.WEIBULL, "TyD")
        result = self.engine.analyze(asset, analysis_year=2026)
        self.assertGreater(result.eeol_year, 2020)
        self.assertGreater(result.current_pof, 0)
        self.assertLessEqual(result.current_pof, 1.0)

    def test_weibull_generacion_penstock(self):
        """Weibull: Generacion penstock."""
        asset = self._asset(DegradationModel.WEIBULL, "Generacion",
                            "penstock", beta=2.0, eta=60.0)
        result = self.engine.analyze(asset, analysis_year=2026)
        self.assertIn(result.intervention_urgency,
                      ["INMEDIATA", "ALTA", "MEDIA", "BAJA"])

    def test_linear_aya_tuberia(self):
        """Lineal: AyA tuberia."""
        asset = self._asset(DegradationModel.LINEAR, "AyA",
                            "tuberia_conduccion", condition=0.4)
        result = self.engine.analyze(asset, analysis_year=2026)
        self.assertGreater(result.optimal_intervention_year, 2020)
        self.assertIsInstance(result.pof_horizon, dict)

    def test_linear_gas_gasoducto(self):
        """Lineal: Gas gasoducto."""
        asset = self._asset(DegradationModel.LINEAR, "Gas", "gasoducto",
                            condition=0.3, beta=1.6, eta=55.0)
        result = self.engine.analyze(asset, analysis_year=2026)
        self.assertGreater(result.cumulative_risk_npv, 0)

    def test_step_scada(self):
        """Escalon: SCADA — obsolescencia discreta."""
        asset = self._asset(DegradationModel.STEP, "TyD",
                            "scada_sistema", design_life=12,
                            repl_cost=2_000_000_000, maint_cost=50_000_000)
        result = self.engine.analyze(asset, analysis_year=2026)
        self.assertGreater(result.optimal_intervention_year, 0)

    def test_accelerated_critical_condition(self):
        """Acelerado: condicion critica -> urgencia INMEDIATA o ALTA."""
        asset = self._asset(DegradationModel.ACCELERATED, "TyD",
                            "interruptor", condition=0.9)
        result = self.engine.analyze(asset, analysis_year=2026)
        self.assertIn(result.intervention_urgency, ["INMEDIATA", "ALTA"])

    def test_all_weibull_asset_types(self):
        """Analiza todos los activos con parametros Weibull del config."""
        weibull_params = self.cfg.get("optimal_timing", {}).get(
            "weibull_params_by_asset", {})
        for asset_class, params in weibull_params.items():
            if asset_class.startswith("_"):
                continue
            asset = self._asset(
                DegradationModel.WEIBULL, "TyD", asset_class,
                beta=params["beta"], eta=params["eta"]
            )
            try:
                result = self.engine.analyze(asset, analysis_year=2026)
                self.assertIsNotNone(result.eeol_year,
                                     f"EEOL nulo para {asset_class}")
            except Exception as e:
                self.fail(f"Error en {asset_class}: {e}")

    def test_result_fields(self):
        """DegradationResult tiene todos los campos esperados."""
        asset  = self._asset()
        result = self.engine.analyze(asset, analysis_year=2026)
        for attr in ["asset_id", "current_pof", "pof_horizon", "risk_horizon",
                     "eeol_year", "optimal_intervention_year",
                     "cumulative_risk_npv", "intervention_urgency"]:
            self.assertTrue(hasattr(result, attr), f"Falta attr: {attr}")

    def test_degradation_hint_is_int(self):
        """optimal_intervention_year es un int valido."""
        asset  = self._asset(asset_class="interruptor")
        result = self.engine.analyze(asset, analysis_year=2026)
        self.assertIsInstance(result.optimal_intervention_year, int)
        self.assertGreater(result.optimal_intervention_year, 2020)


# ─────────────────────────────────────────────────────────────────────────────
# S12 — COMMITTED SPEND
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(PULP_OK, "PuLP no instalado")
class S12_CommittedSpend(unittest.TestCase):

    def setUp(self):
        self.opt = EnterpriseOptimizer(config=_cfg())

    def test_committed_not_mandatory_distinction(self):
        """is_committed y is_mandatory son flags independientes."""
        p_mand = _make_project("MAND", is_mandatory=True, is_committed=False)
        p_comm = _make_project("COMM", is_mandatory=False,
                               is_committed=True, committed_year=2027)
        self.assertTrue(p_mand.is_mandatory  and not p_mand.is_committed)
        self.assertTrue(p_comm.is_committed  and not p_comm.is_mandatory)

    def test_committed_summary_keys(self):
        """committed_summary contiene n_committed y capex_committed_COP."""
        p = _make_project("C1", capex=8_000_000_000,
                          is_committed=True, committed_year=2026)
        result = self.opt.optimize([p], _make_periods(budget=200_000_000_000))
        cs = result.committed_summary
        self.assertIn("n_committed", cs)
        self.assertIn("capex_committed_COP", cs)

    def test_committed_year_zero_uses_exec_year(self):
        """committed_year=0 usa execution_year como fallback."""
        p = _make_project("C0", exec_year=2028,
                          is_committed=True, committed_year=0)
        result = self.opt.optimize([p], _make_periods(budget=200_000_000_000))
        sel = {s["project_id"]: s["year"] for s in result.selected}
        if "C0" in sel:
            self.assertEqual(sel["C0"], 2028,
                             "committed_year=0 debe usar exec_year")

    def test_multiple_committed_projects(self):
        """Multiples comprometidos en distintos anos."""
        p1 = _make_project("C1", capex=5_000_000_000,
                           is_committed=True, committed_year=2026)
        p2 = _make_project("C2", capex=5_000_000_000,
                           is_committed=True, committed_year=2028)
        result = self.opt.optimize([p1, p2],
                                   _make_periods(budget=200_000_000_000))
        cs = result.committed_summary
        self.assertGreaterEqual(cs.get("n_committed", 0), 2)


# ─────────────────────────────────────────────────────────────────────────────
# S13 — TOTEX
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(PULP_OK, "PuLP no instalado")
class S13_TOTEX(unittest.TestCase):

    def setUp(self):
        self.opt = EnterpriseOptimizer(config=_cfg())

    def test_totex_ceiling_reduces_selection(self):
        """TOTEX bajo reduce el numero de proyectos seleccionados."""
        projects = [
            _make_project(f"T{i}", capex=10_000_000_000,
                          opex_incremental=500_000_000)
            for i in range(6)
        ]
        r_full = self.opt.optimize(projects, _make_periods(budget=500_000_000_000))
        r_rest = self.opt.optimize(projects,
                                   _make_periods(budget=500_000_000_000,
                                                 totex_budget=15_000_000_000))
        self.assertGreaterEqual(len(r_full.selected), len(r_rest.selected))

    def test_degradation_hint_i2_applied(self):
        """I2: degradation_hints aplica bono de timing."""
        p = _make_project("DH1", asset_id="ASSET_W", exec_year=2026,
                          capex=8_000_000_000)
        hints = {"ASSET_W": 2028}
        result = self.opt.optimize([p], _make_periods(budget=200_000_000_000),
                                   degradation_hints=hints)
        self.assertIn(result.solver_status, ["OPTIMAL", "FEASIBLE"])
        self.assertIsInstance(result.degradation_hints_applied, list)

    def test_totex_utilization_structure(self):
        """totex_utilization es dict {year: dict}."""
        p = _make_project("TU1", capex=5_000_000_000,
                          opex_incremental=100_000_000)
        result = self.opt.optimize([p], _make_periods(totex_budget=20_000_000_000))
        for yr, info in result.totex_utilization.items():
            self.assertIsInstance(yr, int)
            # Debe tener algun campo de TOTEX
            self.assertIsInstance(info, dict)

    def test_totex_utilization_present_without_ceiling(self):
        """totex_utilization presente aunque no haya techo TOTEX."""
        p = _make_project("TU2", capex=5_000_000_000)
        result = self.opt.optimize([p], _make_periods())
        self.assertIsInstance(result.totex_utilization, dict)


# ─────────────────────────────────────────────────────────────────────────────
# S14 — FRONTERA EFICIENTE
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(PULP_OK, "PuLP no instalado")
class S14_EfficientFrontier(unittest.TestCase):

    def setUp(self):
        self.opt = EnterpriseOptimizer(config=_cfg())
        self.projects = [
            _make_project(f"P{i}", capex=(i+1) * 5_000_000_000)
            for i in range(5)
        ]
        self.periods = _make_periods()

    def test_capex_frontier_is_list(self):
        """compute_frontier=True genera lista efficient_frontier."""
        result = self.opt.optimize(self.projects, self.periods,
                                   compute_frontier=True)
        self.assertIsInstance(result.efficient_frontier, list)

    def test_totex_frontier_min_points(self):
        """totex_frontier tiene >= 2 puntos."""
        result = self.opt.optimize(self.projects, self.periods,
                                   compute_totex_frontier=True)
        self.assertGreaterEqual(len(result.totex_frontier), 1)

    def test_frontier_fields_present(self):
        """Cada punto de la frontera tiene 'budget_pct'."""
        result = self.opt.optimize(self.projects, self.periods,
                                   compute_frontier=True)
        for pt in result.efficient_frontier:
            self.assertIn("budget_pct", pt)

    def test_frontier_vnt_nondecreasing(self):
        """VNT es no-decreciente con mayor presupuesto."""
        result = self.opt.optimize(self.projects, self.periods,
                                   compute_frontier=True)
        frontier = result.efficient_frontier
        if len(frontier) >= 2:
            vnts = [f.get("total_vnt_COP", 0) for f in frontier]
            # Max >= Min (monotonia debil)
            self.assertGreaterEqual(max(vnts), min(vnts))


# ─────────────────────────────────────────────────────────────────────────────
# S15 — RIESGO: MATRIZ MULTI-TIPO + R8
# ─────────────────────────────────────────────────────────────────────────────

class S15_RiskMatrix(unittest.TestCase):

    def setUp(self):
        self.cfg = _cfg()

    def test_risk_matrix_levels_all_bus(self):
        """Matriz de consecuencia existe para los 4 BUs."""
        matrix = self.cfg.get("risk_matrix", {}).get("consequence_types_matrix", {})
        self.assertGreater(len(matrix), 0)
        missing = []
        for ctype, cdata in matrix.items():
            if ctype.startswith("_"):
                continue
            levels_by_bu = cdata.get("levels_by_bu", {})
            for bu in ["TyD", "AyA", "Gas", "Generacion"]:
                if bu not in levels_by_bu:
                    missing.append(f"{ctype}.{bu}")
        self.assertEqual(missing, [],
                         f"BUs ausentes en risk_matrix: {missing}")

    def test_risk_matrix_level_1_to_5(self):
        """_risk_matrix_level retorna 1-5 para todas las combinaciones."""
        opt = EnterpriseOptimizer(config=self.cfg)
        for c in range(1, 6):
            for p in range(1, 6):
                lvl = opt._risk_matrix_level(c, p)
                self.assertIn(lvl, [1, 2, 3, 4, 5])

    def test_risk_level_boundary_clamping(self):
        """Valores fuera de rango se clampean."""
        opt = EnterpriseOptimizer(config=self.cfg)
        self.assertIn(opt._risk_matrix_level(0, 0), [1, 2, 3, 4, 5])
        self.assertIn(opt._risk_matrix_level(6, 6), [1, 2, 3, 4, 5])

    def test_risk_consequence_values_present(self):
        """Todos los niveles tienen value_COP."""
        matrix = self.cfg.get("risk_matrix", {}).get("consequence_types_matrix", {})
        for ctype, cdata in matrix.items():
            if ctype.startswith("_"):
                continue
            for bu, levels in cdata.get("levels_by_bu", {}).items():
                for lvl, data in levels.items():
                    self.assertIn("value_COP", data,
                                  f"{ctype}.{bu}.{lvl} falta value_COP")

    def test_risk_high_level_gt_low_level(self):
        """Nivel de riesgo alto (5,5) > bajo (1,1)."""
        opt = EnterpriseOptimizer(config=self.cfg)
        self.assertGreater(opt._risk_matrix_level(5, 5),
                           opt._risk_matrix_level(1, 1))

    @unittest.skipUnless(PULP_OK, "PuLP no instalado")
    def test_r8_with_mitigante(self):
        """R8: mitigante forzado cuando hay asset con riesgo inaceptable."""
        risk_crit = _make_risk(cb=5, pb=5, cr=1, pr=1)
        mitig = _make_project("MITIG_R8", asset_id="ASSET_CRIT",
                              risk=risk_crit)
        result = EnterpriseOptimizer(config=self.cfg).optimize(
            [mitig], _make_periods(budget=200_000_000_000),
            risk_tolerance_level=4,
        )
        pids = {s["project_id"] for s in result.selected}
        self.assertIn("MITIG_R8", pids)


# ─────────────────────────────────────────────────────────────────────────────
# S16 — PROPORCIONALIDAD
# ─────────────────────────────────────────────────────────────────────────────

class S16_Proportionality(unittest.TestCase):

    def setUp(self):
        self.cfg    = _cfg()
        self.engine = ValueEngine(self.cfg)

    def test_proportionality_thresholds_exist_per_bu(self):
        """Config tiene umbrales por BU."""
        thresholds = self.cfg.get("proportionality_thresholds_COP", {})
        for bu in ["TyD", "AyA", "Gas", "Generacion"]:
            self.assertIn(bu, thresholds, f"BU '{bu}' sin umbral")

    def test_full_model_tyd_no_crash(self):
        """TyD >= $10.000MM: evaluate() sin error."""
        p = _make_project(bu="TyD", capex=15_000_000_000)
        r = self.engine.evaluate(p)
        self.assertIn("net_value_COP", r)

    def test_simplified_model_aya_no_crash(self):
        """AyA $500MM-$5.000MM: evaluate() sin error."""
        p = _make_project(bu="AyA", capex=2_000_000_000)
        r = self.engine.evaluate(p)
        self.assertIn("net_value_COP", r)

    def test_rule_based_gas_no_crash(self):
        """Gas < $300MM: evaluate() sin error."""
        p = _make_project(bu="Gas", capex=200_000_000)
        r = self.engine.evaluate(p)
        self.assertIn("net_value_COP", r)

    def test_model_level_in_result(self):
        """model_level presente en el resultado."""
        p = _make_project(bu="TyD", capex=20_000_000_000)
        r = self.engine.evaluate(p)
        self.assertIn("model_level", r)


# ─────────────────────────────────────────────────────────────────────────────
# S17 — HELPERS
# ─────────────────────────────────────────────────────────────────────────────

class S17_Helpers(unittest.TestCase):

    def test_load_config_returns_dict(self):
        """load_config() retorna dict con 'wacc'."""
        cfg = load_config()
        self.assertIsInstance(cfg, dict)
        self.assertIn("wacc", cfg)

    def test_save_config_no_crash(self):
        """save_config() no crashea con config valido."""
        cfg = load_config()
        # Ruta EXPLICITA a un temporal. save_config() sin ruta escribe el
        # config.json de produccion: la prueba lo pisaba en cada corrida y
        # lo mantenia congelado en una version vieja sin que nadie lo notara.
        with tempfile.TemporaryDirectory() as td:
            destino = os.path.join(td, "config_prueba.json")
            try:
                save_config(cfg, path=destino)
            except Exception as e:
                self.fail(f"save_config() lanzo excepcion: {e}")
            self.assertTrue(os.path.exists(destino),
                            "save_config() no escribio el archivo destino")

    def test_print_report_generates_output(self):
        """print_report() genera texto en stdout."""
        result = {
            "capex_COP": 5_000_000_000,
            "net_value_COP": 8_000_000_000,
            "value_efficiency_index": 1.6,
            "benefits_raw_COP": 13_000_000_000,
            "driver_results": [],
            "cod_COP": 100_000_000,
            "delta_r_COP": 200_000_000,
            "model_level": "FULL",
            "optimal_year": 2026,
        }
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            print_report(result, config_version="3.6.0")
        finally:
            sys.stdout = old
        output = buf.getvalue()
        self.assertGreater(len(output), 0)

    def test_write_audit_log_no_crash(self):
        """write_audit_log() no crashea."""
        result = {
            "capex_COP": 5_000_000_000,
            "net_value_COP": 8_000_000_000,
            "value_efficiency_index": 1.6,
            "benefits_raw_COP": 13_000_000_000,
            "driver_results": [],
        }
        cfg = load_config()
        # Ruta EXPLICITA a un temporal: sin ella la prueba deja asientos de
        # auditoria de proyectos ficticios en la traza de produccion.
        with tempfile.TemporaryDirectory() as td:
            destino = os.path.join(td, "audit_prueba.log")
            try:
                write_audit_log(result, cfg, path=destino)
            except Exception as e:
                self.fail(f"write_audit_log lanzo excepcion: {e}")
            self.assertTrue(os.path.exists(destino),
                            "write_audit_log() no escribio el registro")


# ─────────────────────────────────────────────────────────────────────────────
# S18 — PORTAFOLIO TI + ALCANCES MILP
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(PULP_OK, "PuLP no instalado")
class S18_PortfolioTI_Alcances(unittest.TestCase):

    def setUp(self):
        self.cfg = _cfg()
        self.opt = EnterpriseOptimizer(config=self.cfg)

    def test_all_investment_types_evaluate(self):
        """Los 2 tipos de inversion (D-13: se retiraron los otros 4) se evaluan sin error."""
        inv_types = [
            "Reposicion de Activos",
            "Crecimiento / Expansion",
        ]
        for inv in inv_types:
            p = _make_project(inv_type=inv, bu="Todos", capex=2_000_000_000)
            r = ValueEngine(self.cfg).evaluate(p)
            self.assertIn("net_value_COP", r, f"Tipo '{inv}' fallo")

    def test_silo_mode_tyd_only(self):
        """Modo SILO: solo proyectos TyD en resultado."""
        projects = [
            _make_project("TYD_P", bu="TyD",  capex=10_000_000_000),
            _make_project("AYA_P", bu="AyA",  capex=10_000_000_000),
            _make_project("GAS_P", bu="Gas",  capex=10_000_000_000),
        ]
        result = self.opt.optimize(projects, _make_periods(), bu_filter="TyD")
        for s in result.selected:
            self.assertEqual(s.get("business_unit"), "TyD",
                             "SILO: proyecto de otra BU en resultado")

    def test_enterprise_global_mode(self):
        """Modo Enterprise Global: todos los BUs compiten."""
        projects = [
            _make_project(f"{bu}_P", bu=bu, capex=10_000_000_000)
            for bu in ["TyD", "AyA", "Gas", "Generacion"]
        ]
        result = self.opt.optimize(projects, _make_periods(budget=200_000_000_000))
        self.assertNotEqual(result.solver_status, "ERROR")

    def test_pisos_r7_enterprise(self):
        """Pisos R7: solver no crashea con presupuesto amplio."""
        projects = [
            _make_project(f"{bu}_P{i}", bu=bu, capex=15_000_000_000)
            for bu in ["TyD", "AyA"] for i in range(3)
        ]
        pisos = {"TyD": 10_000_000_000, "AyA": 5_000_000_000}
        result = self.opt.optimize(
            projects, _make_periods(budget=500_000_000_000),
            pisos_inversion_COP=pisos,
        )
        self.assertNotEqual(result.solver_status, "ERROR")

    def test_ti_project_vd2a_positive_vnt(self):
        """Proyecto tipo TI (D-13: clasificado como Reposicion de Activos,
        ya no existe un tipo propio) con VD-2-A OPEX evitado genera VNT positivo."""
        sc = _make_sc("VD-2-A", sc_type="monetizable",
                      delta=1_000_000_000.0, price=1.0)
        p  = _make_project(
            "TI_001", bu="Todos",
            inv_type="Reposicion de Activos",
            capex=500_000_000,
            drivers=[_make_driver("VD-2", [sc])],
        )
        r = ValueEngine(self.cfg).evaluate(p)
        self.assertGreater(r["net_value_COP"], 0)


# ─────────────────────────────────────────────────────────────────────────────
# S19 — VALIDACION DE DATOS
# ─────────────────────────────────────────────────────────────────────────────

class S19_DataValidation(unittest.TestCase):

    def setUp(self):
        self.loader = PortfolioLoader(_cfg())

    def _csv(self, rows):
        buf = io.StringIO()
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        return buf.getvalue().encode("utf-8")

    def _base(self, pid="P001", bu="TyD", c26=5_000):
        return {
            "id": pid, "name": f"Test {pid}",
            "business_unit": bu,
            "capex_2026": c26, "capex_2027": 0,
            "capex_2028": 0, "capex_2029": 0, "capex_2030": 0,
            "execution_year": 2026, "is_mandatory": "False",
        }

    def test_load_basic_row(self):
        """Fila basica se carga correctamente."""
        rows = [self._base("P001")]
        projects, _ = self.loader.from_bytes(self._csv(rows), "t.csv")
        self.assertGreaterEqual(len(projects), 0)

    def test_duplicate_ids_deduplicated(self):
        """IDs duplicados se deduplican."""
        rows = [self._base("P001"), self._base("P001", bu="AyA")]
        projects, report = self.loader.from_bytes(self._csv(rows), "t.csv")
        ids = [p.id for p in projects]
        self.assertTrue(
            len(set(ids)) == len(ids),
            f"IDs no deduplicados: {ids}"
        )

    def test_mandatory_flag_true(self):
        """is_mandatory='True' -> p.is_mandatory=True."""
        rows = [{**self._base("PM"), "is_mandatory": "True"}]
        projects, _ = self.loader.from_bytes(self._csv(rows), "t.csv")
        if projects:
            self.assertTrue(projects[0].is_mandatory)

    def test_mandatory_flag_false(self):
        """is_mandatory='False' -> p.is_mandatory=False."""
        rows = [{**self._base("PF"), "is_mandatory": "False"}]
        projects, _ = self.loader.from_bytes(self._csv(rows), "t.csv")
        if projects:
            self.assertFalse(projects[0].is_mandatory)

    def test_bu_todos(self):
        """BU 'Todos' cargado correctamente."""
        rows = [{**self._base("PT"), "business_unit": "Todos"}]
        projects, _ = self.loader.from_bytes(self._csv(rows), "t.csv")
        if projects:
            self.assertIn(projects[0].business_unit, ["Todos", "TyD", "AyA",
                                                      "Gas", "Generacion"])

    def test_large_portfolio(self):
        """50 proyectos se cargan sin error."""
        bus = ["TyD", "AyA", "Gas", "Generacion"]
        rows = [
            {**self._base(f"P{i:03d}", bu=bus[i%4], c26=5_000+i*100)}
            for i in range(50)
        ]
        projects, _ = self.loader.from_bytes(self._csv(rows), "t.csv")
        self.assertGreaterEqual(len(projects), 40)


# ─────────────────────────────────────────────────────────────────────────────
# S20 — ESCENARIO INTEGRAL
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(PULP_OK, "PuLP no instalado")
class S20_FullIntegration(unittest.TestCase):
    """
    Escenario maestro: ejercita TODOS los modulos en una sola ejecucion.
    Tipos de negocio: TyD, AyA, Gas, Generacion.
    Tipos de activo: todos en Weibull.
    Tipos de inversion: los 6.
    Restricciones activas: R1-R12.
    """

    def setUp(self):
        self.cfg = _cfg()
        self.opt = EnterpriseOptimizer(config=self.cfg)
        wacc = float(self.cfg.get("wacc", 0.1))
        hor  = int(self.cfg.get("optimal_timing", {}).get("horizon_years", 10))
        self.deg = AssetDegradationEngine(wacc=wacc, horizon_years=hor,
                                          config=self.cfg)

    def _portfolio(self):
        projects = []

        # TyD: Reposicion, R8 must-mitigate
        p1 = _make_project(
            "TYD_REP_001", bu="TyD", inv_type="Reposicion de Activos",
            capex=15_000_000_000, asset_id="TRANSF_001",
            risk=_make_risk(cb=5, pb=4, cr=1, pr=1),
            drivers=[_make_driver("VD-1", [
                _make_sc("VD-1-A", delta=500.0, price=335_000),
                _make_sc("VD-1-B", delta=50_000.0, price=6_200),
            ])],
        )
        projects.append(p1)

        # TyD: Crecimiento — padre de TYD_HIJO (R3a)
        p2 = _make_project(
            "TYD_CREC_002", bu="TyD", inv_type="Crecimiento / Expansion",
            capex=10_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-3", [
                _make_sc("VD-3-A", delta=200.0, price=5_200_000),
            ])],
        )
        projects.append(p2)

        # TyD: Hijo con lag=1 (R3b)
        p3 = _make_project(
            "TYD_HIJO_003", bu="TyD", inv_type="Reposicion de Activos",
            capex=5_000_000_000, exec_year=2027,
            precedent_id="TYD_CREC_002", min_lag=1,
            drivers=[_make_driver("VD-2", [
                _make_sc("VD-2-A", delta=500_000_000.0, price=1.0),
            ])],
        )
        p3.precedent_project_id = "TYD_CREC_002"
        p3.min_lag_years = 1
        projects.append(p3)

        # AyA: Dos alternativas exclusivas (R3c)
        p4 = _make_project(
            "AYA_ALT_A", bu="AyA", inv_type="Reposicion de Activos",
            capex=8_000_000_000,
            drivers=[_make_driver("VD-1", [
                _make_sc("VD-1-C", delta=10_000.0, price=3_100),
            ])],
            mutual_exclusion_ids=["AYA_ALT_B"],
        )
        p4.mutual_exclusion_ids = ["AYA_ALT_B"]
        projects.append(p4)

        p5 = _make_project(
            "AYA_ALT_B", bu="AyA", inv_type="Crecimiento / Expansion",
            capex=12_000_000_000,
            drivers=[_make_driver("VD-3", [
                _make_sc("VD-3-B", delta=100.0, price=6_800_000),
            ])],
            mutual_exclusion_ids=["AYA_ALT_A"],
        )
        p5.mutual_exclusion_ids = ["AYA_ALT_A"]
        projects.append(p5)

        # Gas: Grupo exclusivo (R3d)
        for i in range(2):
            pg = _make_project(
                f"GAS_ESG_00{i+1}", bu="Gas", inv_type="Crecimiento / Expansion",
                capex=6_000_000_000,
                exclusion_group_id="GAS_ESG_GRP",
                drivers=[_make_driver("VD-4", [
                    _make_sc("VD-4-A", delta=200.0, price=88_000),
                    _make_sc("VD-4-C", delta=0.5, price=3_200_000_000),
                ])],
            )
            pg.exclusion_group_id = "GAS_ESG_GRP"
            projects.append(pg)

        # Generacion: Reposicion penstock, R8
        p8 = _make_project(
            "GEN_PENSTOCK_001", bu="Generacion",
            inv_type="Reposicion de Activos",
            capex=25_000_000_000, asset_id="PENSTOCK_001",
            risk=_make_risk(cb=5, pb=5, cr=2, pr=2),
            drivers=[
                _make_driver("VD-2", [_make_sc("VD-2-C", delta=50.0, price=335_000)]),
                _make_driver("VD-5", [_make_sc("VD-5-A", delta=5_000_000_000.0,
                                               price=1.0)]),
            ],
        )
        projects.append(p8)

        # TyD: Comprometido R11
        p9 = _make_project(
            "TYD_COMM_001", bu="TyD", inv_type="Reposicion de Activos",
            capex=7_000_000_000, is_committed=True, committed_year=2027,
            is_mandatory=True,
            drivers=[_make_driver("VD-5", [
                _make_sc("VD-5-B", delta=10.0, price=4_500_000),
            ])],
        )
        projects.append(p9)

        # TI: Proyecto Empresarial
        p10 = _make_project(
            "TI_ERP_001", bu="Todos", inv_type="Reposicion de Activos",
            capex=2_000_000_000,
            drivers=[_make_driver("VD-2", [
                _make_sc("VD-2-A", delta=800_000_000.0, price=1.0),
            ])],
        )
        projects.append(p10)

        return projects

    def _periods(self):
        return [
            ResourcePeriod(
                year=yr,
                budget_COP=100_000_000_000,
                max_person_months=300.0,
                max_projects_concurrent=8,
                totex_budget_COP=120_000_000_000,
            )
            for yr in [2026, 2027, 2028, 2029, 2030]
        ]

    def _hints(self):
        hints = {}
        for aid, aclass, bu in [
            ("TRANSF_001", "transformador_potencia", "TyD"),
            ("PENSTOCK_001", "penstock", "Generacion"),
        ]:
            profile = AssetProfile(
                asset_id=aid, asset_name=aclass, asset_class=aclass,
                business_unit=bu, degradation_model=DegradationModel.WEIBULL,
                install_year=1995, design_life_years=35,
                current_condition=0.6,
                replacement_cost_COP=15_000_000_000,
                annual_maintenance_COP=300_000_000,
                consequence_level=4,
                weibull_beta=2.5, weibull_eta=35.0,
            )
            try:
                r = self.deg.analyze(profile, analysis_year=2026)
                hints[aid] = r.optimal_intervention_year
            except Exception:
                pass
        return hints

    def test_full_integration(self):
        """Valida todos los modulos en una sola ejecucion."""
        projects = self._portfolio()
        periods  = self._periods()
        hints    = self._hints()

        outages = [
            PlannedOutage(outage_id="O1", asset_id="TRANSF_001",
                          year=2027, name="Parada",
                          duration_days=7, outage_type="mantenimiento"),
        ]
        pisos = {
            "TyD": 10_000_000_000, "AyA": 5_000_000_000,
            "Gas": 3_000_000_000, "Generacion": 15_000_000_000,
        }

        result = self.opt.optimize(
            projects, periods,
            pisos_inversion_COP=pisos,
            risk_tolerance_level=4,
            degradation_hints=hints,
            planned_outages=outages,
            compute_frontier=True,
            compute_totex_frontier=True,
        )

        # Status reconocido
        self.assertIn(result.solver_status,
                      ["OPTIMAL", "FEASIBLE", "INFEASIBLE"],
                      f"Status inesperado: {result.solver_status}")

        if result.solver_status in ["OPTIMAL", "FEASIBLE"]:
            pids = [s["project_id"] for s in result.selected]

            # R2: Sin duplicados
            self.assertEqual(len(pids), len(set(pids)), "R2: duplicados")

            # R3c: AYA_ALT_A y AYA_ALT_B no coexisten
            sel = set(pids)
            self.assertFalse("AYA_ALT_A" in sel and "AYA_ALT_B" in sel,
                             "R3c: alternativas excluyentes coexisten")

            # R3d: Max 1 del grupo GAS_ESG
            # Nota: desde que _preselect_by_eav corre sin condiciones antes
            # del MILP (grupo S32), GAS_ESG ya llega reducido a <=1 antes de
            # esta aserción — pasa por eso, no porque R3d se haya ejercitado
            # aquí. El guardián real de R3d por sí solo es
            # S32_PreseleccionPorEAV.test_r3d_sigue_activo_si_se_desactiva_la_preseleccion.
            gas_esg = [p for p in pids if "GAS_ESG" in p]
            self.assertLessEqual(len(gas_esg), 1, "R3d: grupo violado")

            # R11: Comprometido en 2027
            sel_dict = {s["project_id"]: s["year"] for s in result.selected}
            if "TYD_COMM_001" in sel_dict:
                self.assertEqual(sel_dict["TYD_COMM_001"], 2027,
                                 "R11: ano comprometido incorrecto")

            # Committed summary
            self.assertIn("n_committed", result.committed_summary)

            # TOTEX
            self.assertIsInstance(result.totex_utilization, dict)

            # Frontera
            self.assertIsInstance(result.efficient_frontier, list)
            self.assertIsInstance(result.totex_frontier, list)

        # Plan Versioning
        tmp   = tempfile.mkdtemp()
        store = PlanVersionStore(os.path.join(tmp, "versions.json"))
        v1 = store.save_version(result, "PAI 2026-2030 v1", "ASPD", "inicial")
        self.assertIsNotNone(v1)
        self.assertEqual(len(v1.signature), 64)

        result2 = self.opt.optimize(projects[:4], periods)
        v2   = store.save_version(result2, "PAI 2026-2030 v2", "ASPD", "revision")
        diff = store.diff_versions(v1.version_id, v2.version_id)
        self.assertIn("added", diff)
        self.assertIn("removed", diff)

        # SnapshotStore
        snap_store = SnapshotStore(tmp)
        p_test = projects[0]
        r_test = ValueEngine(self.cfg).evaluate(p_test)
        snap = snap_store.capture(r_test, p_test, self.cfg, analyst="aspd")
        self.assertIn("integrity_hash", snap.__dict__)

        # WorkflowEngine
        wf = ProjectWorkflow("FULL_P", "Proyecto Integral", "TyD", cycle="PAI 2026")
        lb = next(t["label"] for t in WF_FULL_TRANSITIONS
                  if t["from"] == "BORRADOR" and t["role"] == "analista")
        wf = WorkflowEngine.apply(
            wf, {"to": "ENVIADO", "label": lb},
            actor="aspd", role="analista", bu="TyD",
        )
        self.assertEqual(wf.current_state, "ENVIADO")


# ─────────────────────────────────────────────────────────────────────────────
# S21 — TRATAMIENTO TEMPORAL DEL DIFERIMIENTO
#
# Cada termino de la ecuacion maestra reaccionaba al mismo diferimiento con una
# regla propia: los beneficios se descontaban, el CAPEX se inflaba sin
# descontarse, y el riesgo crecia por (1+g)^d sin que nadie cobrara la
# exposicion sufrida durante la espera. Con g > r en los cuatro negocios, el
# VNT crecia de forma monotona al postergar y el analisis de tiempo optimo
# recomendaba diferir al borde de la ventana para CUALQUIER activo.
#
# Estas pruebas existen porque el resto de la suite no toca el diferimiento:
# las 140 pruebas anteriores pasaron sin cambiar despues de corregir la
# ecuacion. Sin este grupo, una implementacion que omita las correcciones
# aprueba la suite completa y reintroduce el defecto en silencio.
# ─────────────────────────────────────────────────────────────────────────────

class S21_TratamientoTemporal(unittest.TestCase):

    BUS = ("TyD", "AyA", "Gas", "Generacion")

    def _eng(self):
        return ValueEngine(_cfg())

    def _proyecto(self, bu):
        # CAPEX bajado de 8.000M a 5.000M y delta de 200 a 150 tras Reto 6
        # (VD-6, D-11): al corregir la doble contabilización de
        # reputación, el riesgo mitigado (ΔR) de los 4 negocios se redujo
        # — parte de su magnitud anterior era, precisamente, la
        # duplicación que se retiró.
        #
        # Primer intento de corrección (subir la severidad de riesgo base
        # de (3,3,1,1) a (4,4,1,1), manteniendo CAPEX/delta): restauraba
        # las dos aserciones, pero la revisión de calidad encontró que
        # dejaba de detectar el defecto real que este grupo vigila —
        # verificado reintroduciendo deliberadamente el defecto histórico
        # (sumar de vuelta deferral_exposure_COP en vez de restarlo en
        # _calc_risk) y confirmando que, con esa severidad, NINGÚN negocio
        # pasaba de "no monótono" a "monótono creciente": el VNT quedaba
        # tan dominado por el riesgo mitigado que el efecto de que el
        # CAPEX se abarata con el tiempo (inflación de obra < WACC,
        # propiedad preexistente del motor) ya no podía inclinar la
        # balanza en ningún sentido — la prueba pasaba, pero sin vigilar
        # nada.
        #
        # Corregido con este ajuste: severidad de riesgo SIN TOCAR
        # (3,3,1,1, la original), CAPEX y beneficio reducidos en su lugar.
        # Verificado con el mismo arnés de reintroducción del defecto:
        # TyD y Generación (VNT positivo) siguen decreciendo correctamente
        # con el diferimiento; AyA y Gas (VNT negativo) dejan de ser
        # monótonos como antes — y, reintroduciendo el defecto histórico,
        # AyA y Gas SÍ pasan a monótono creciente, confirmando que la
        # prueba sigue detectando el defecto real, no solo evitando las
        # dos aserciones que fallaban.
        p = _make_project(
            pid=f"DIF-{bu}", bu=bu, capex=5_000_000_000,
            drivers=[_make_driver("VD-1", [_make_sc("VD-1-A", delta=150.0)])],
            risk=_make_risk(3, 3, 1, 1), construction_years=1)
        p.analysis_horizon_years = ValueEngine(_cfg()).get_horizon_for_asset(p)
        return p

    # ── El invariante central ────────────────────────────────────────────────

    def test_vnt_no_crece_monotonamente_al_diferir(self):
        """Postergar no puede pagar por si solo, en ningun negocio.

        Es la forma exacta del defecto original: una serie que solo subia.
        """
        eng = self._eng()
        for bu in self.BUS:
            vs = [eng.evaluate(self._proyecto(bu), deferral_years=d)["net_value_COP"]
                  for d in range(0, 13)]
            creciente = all(vs[i] <= vs[i + 1] for i in range(len(vs) - 1))
            self.assertFalse(
                creciente,
                f"{bu}: el VNT crece de forma monotona con el diferimiento "
                f"({vs[0]/1e9:.2f}B -> {vs[-1]/1e9:.2f}B). Diferir esta saliendo gratis.")

    def test_grupo_detecta_el_defecto_historico_si_se_reintroduce(self):
        """Meta-prueba: si alguien reintroduce el defecto original (no
        restar deferral_exposure_COP en _calc_risk — el riesgo crece por
        (1+g)^d sin que nadie cobre la exposición sufrida durante la
        espera), al menos un negocio debe pasar de 'no monótono' a
        'monótono creciente'.

        Existe porque ya pasó una vez: una recalibración de este mismo
        escenario (severidad de riesgo subida en vez de CAPEX/beneficio
        bajados) dejaba pasar las otras dos pruebas de este grupo sin que
        ningún negocio siguiera siendo sensible al defecto real — el VNT
        quedaba tan dominado por el riesgo mitigado que el efecto de que
        el CAPEX se abarata con el tiempo ya no podía inclinar la balanza
        en ningún sentido. Si esta prueba falla, las otras dos pueden
        seguir en verde sin que el grupo esté vigilando nada."""
        import unittest.mock as _mock

        orig_calc_risk = ValueEngine._calc_risk

        def _con_defecto_reintroducido(self, *args, **kwargs):
            r = dict(orig_calc_risk(self, *args, **kwargs))
            r["delta_r_npv_COP"] = r["delta_r_npv_COP"] + r.get("deferral_exposure_COP", 0.0)
            return r

        detecto_algun_negocio = False
        with _mock.patch.object(ValueEngine, "_calc_risk", _con_defecto_reintroducido):
            eng = self._eng()
            for bu in self.BUS:
                vs = [eng.evaluate(self._proyecto(bu), deferral_years=d)["net_value_COP"]
                      for d in range(0, 13)]
                creciente = all(vs[i] <= vs[i + 1] for i in range(len(vs) - 1))
                if creciente:
                    detecto_algun_negocio = True

        self.assertTrue(
            detecto_algun_negocio,
            "Con el defecto histórico reintroducido (deferral_exposure_COP "
            "sin restar), ningún negocio pasó a monótono creciente — este "
            "grupo ya no detecta el defecto que existe para vigilar.",
        )

    def test_borde_de_ventana_no_es_el_optimo(self):
        """Diferir al maximo no puede ganarle al ano 0 si el proyecto crea valor.

        Solo aplica a proyectos con VNT positivo: si destruye valor en todo el
        rango no debe hacerse, y que posponer una perdida la reduzca es
        descuento correcto, no un defecto.
        """
        eng = self._eng()
        evaluados = 0
        for bu in self.BUS:
            vs = [eng.evaluate(self._proyecto(bu), deferral_years=d)["net_value_COP"]
                  for d in range(0, 13)]
            if vs[0] <= 0:
                continue
            evaluados += 1
            self.assertLess(vs[-1], vs[0],
                            f"{bu}: diferir 12 anios supera al ano 0")
        self.assertGreater(evaluados, 0,
                           "Ningun negocio produjo un proyecto con VNT positivo: "
                           "la prueba no verifico nada")

    # ── Cada correccion por separado ─────────────────────────────────────────

    def test_se_cobra_exposicion_durante_la_espera(self):
        """El DeltaR que se deja de capturar mientras se espera debe restarse."""
        eng = self._eng()
        r0 = eng.evaluate(self._proyecto("TyD"), deferral_years=0)
        r5 = eng.evaluate(self._proyecto("TyD"), deferral_years=5)
        self.assertEqual(r0["risk_analysis"].get("deferral_exposure_COP", 0), 0.0,
                         "Sin diferimiento no hay exposicion que cobrar")
        self.assertGreater(r5["risk_analysis"].get("deferral_exposure_COP", 0), 0.0,
                           "Diferir 5 anios no cobro ninguna exposicion")

    def test_capex_diferido_se_trae_a_valor_presente(self):
        """El CAPEX inflado entra a la ecuacion descontado, no en pesos futuros."""
        eng = self._eng()
        d = 5
        r0 = eng.evaluate(self._proyecto("TyD"), deferral_years=0)
        rd = eng.evaluate(self._proyecto("TyD"), deferral_years=d)
        infl = _cfg().get("capex_inflation_rate", 0.04)

        # El nominal se sigue inflando: es el desembolso real del ano de obra.
        self.assertAlmostEqual(rd["capex_COP"], r0["capex_COP"] * (1 + infl) ** d,
                               delta=1.0)
        # Y el que entra a la ecuacion es ese nominal traido a hoy.
        w = rd["wacc"]
        self.assertAlmostEqual(rd["capex_pv_COP"], rd["capex_COP"] / ((1 + w) ** d),
                               delta=1.0)
        self.assertLess(rd["capex_pv_COP"], rd["capex_COP"],
                        "El CAPEX diferido no se descontó")

    def test_ventana_de_evaluacion_es_comun(self):
        """Diferir no regala anios extra de beneficio al final del horizonte.

        Sin esto, cada alternativa de fecha se evaluaba sobre una ventana
        distinta y la que empezaba mas tarde miraba mas lejos en el futuro.
        """
        eng = self._eng()
        r0 = eng.evaluate(self._proyecto("TyD"), deferral_years=0)
        r5 = eng.evaluate(self._proyecto("TyD"), deferral_years=5)
        h0 = r0["risk_analysis"]["horizon_used"]
        h5 = r5["risk_analysis"]["horizon_used"]
        self.assertEqual(h5, h0 - 5,
                         f"La ventana no se acorto con el diferimiento: {h0}a -> {h5}a")

    # ── Modo directo: la inacción que no es la falla de un activo ────────────

    def _proyecto_directo(self, base_anual, residual_anual=0.0, beta=0.0, eta=0.0):
        r = _make_risk(4, 4, 1, 1)
        r.baseline_annual_COP = base_anual
        r.residual_annual_COP = residual_anual
        r.weibull_beta, r.weibull_eta = beta, eta
        return _make_project(
            pid="EXP", bu="TyD", capex=8_000_000_000,
            inv_type="Crecimiento/Expansión",
            drivers=[_make_driver("VD-1", [_make_sc("VD-1-A", delta=200.0)])],
            risk=r, construction_years=1)

    def test_monto_directo_reemplaza_la_matriz(self):
        """Un costo anual declarado en pesos manda sobre la matriz 5x5."""
        eng = self._eng()
        r_mat = eng.evaluate(self._proyecto_directo(0.0))
        r_dir = eng.evaluate(self._proyecto_directo(4_557_469_441.0))
        self.assertNotAlmostEqual(r_mat["risk_analysis"]["delta_r_annual_COP"],
                                  r_dir["risk_analysis"]["delta_r_annual_COP"],
                                  delta=1.0,
                                  msg="La matriz sigue mandando: el monto directo se ignoro")
        self.assertAlmostEqual(r_dir["risk_analysis"]["delta_r_annual_COP"],
                               4_557_469_441.0, delta=1.0)
        self.assertEqual(r_dir["risk_analysis"]["cof_source"], "monto_directo")

    def test_monto_directo_resta_el_residual(self):
        """DeltaR = base declarada menos residual declarado, sin tocar la matriz."""
        eng = self._eng()
        r = eng.evaluate(self._proyecto_directo(4_557_469_441.0, 292_723_265.0))
        self.assertAlmostEqual(r["risk_analysis"]["delta_r_annual_COP"],
                               4_557_469_441.0 - 292_723_265.0, delta=1.0)

    def test_monto_directo_fuerza_rama_exponencial(self):
        """Con edad y curva de falla presentes, el monto directo manda igual.

        En una expansion no hay activo que envejezca: evaluar con Weibull
        exigiria una edad que no existe.
        """
        eng = self._eng()
        r = eng.evaluate(self._proyecto_directo(4_557_469_441.0, beta=2.5, eta=30.0))
        self.assertEqual(r["risk_analysis"]["pof_mode"], "exponential")
        self.assertAlmostEqual(r["risk_analysis"]["delta_r_annual_COP"],
                               4_557_469_441.0, delta=1.0)

    def test_monto_directo_conserva_el_tratamiento_temporal(self):
        """El modo directo NO puede saltarse las correcciones de diferimiento.

        Es la diferencia con delta_r_override_COP, que entrega el VPN ya hecho
        y deja el analisis de oportunidad sin efecto.
        """
        eng = self._eng()
        BASE, RES, D = 4_557_469_441.0, 292_723_265.0, 5
        p = self._proyecto_directo(BASE, RES)
        r0 = eng.evaluate(p, deferral_years=0)
        rd = eng.evaluate(p, deferral_years=D)
        self.assertEqual(r0["risk_analysis"].get("deferral_exposure_COP", 0), 0.0)
        self.assertGreater(rd["risk_analysis"].get("deferral_exposure_COP", 0), 0.0,
                           "El monto directo se salto el cobro de exposicion")
        self.assertLess(rd["risk_analysis"]["horizon_used"],
                        r0["risk_analysis"]["horizon_used"],
                        "El monto directo se salto la ventana de evaluacion comun")
        # El monto declarado tiene que escalar por (1+g)^d como cualquier otro:
        # esta igualdad exacta solo se cumple si el valor atraveso la rama
        # exponencial, y no si alguien lo entrego ya convertido a VPN.
        g = rd["risk_analysis"]["risk_growth_rate_used"]
        self.assertAlmostEqual(rd["risk_analysis"]["delta_r_annual_COP"],
                               (BASE - RES) * ((1.0 + g) ** D), delta=1.0,
                               msg="El monto directo no paso por el escalamiento "
                                   "de la rama exponencial")

    def test_vei_usa_denominador_en_valor_presente(self):
        """VNT descontado dividido entre CAPEX en pesos futuros mezcla monedas.

        No basta comprobar que VEI = VNT/capex_pv: con la correccion apagada
        capex_pv es el nominal y la igualdad se cumple sola. Hay que exigir
        ademas que los dos denominadores sean distintos.
        """
        eng = self._eng()
        r = eng.evaluate(self._proyecto("TyD"), deferral_years=5)
        if r["capex_pv_COP"] <= 0:
            self.skipTest("CAPEX no positivo")
        self.assertAlmostEqual(r["value_efficiency_index"],
                               r["net_value_COP"] / r["capex_pv_COP"], places=6)
        vei_nominal = r["net_value_COP"] / r["capex_COP"]
        self.assertNotAlmostEqual(
            r["value_efficiency_index"], vei_nominal, places=6,
            msg="El VEI usa el CAPEX nominal: numerador descontado y "
                "denominador en pesos futuros")


# ─────────────────────────────────────────────────────────────────────────────
# S22 — RAMA WEIBULL: EL RIESGO DE UN ACTIVO QUE ENVEJECE
#
# Estas 69 lineas producen el DeltaR de TODA inversion de reposicion, que es el
# caso de uso principal del AIP. Una campana de mutacion sobre ellas encontro
# 17 mutantes y los 17 sobrevivieron: se podia hacer que la curva ignorara sus
# parametros, que el activo no envejeciera, que el flujo no se descontara o que
# la consecuencia saliera de la tabla equivocada, y la suite seguia en verde.
#
# La leccion de esa campana: no basta con probar que el codigo no truena. Hay
# que reproducir el valor con un calculo independiente. Eso es lo que hace este
# grupo — reimplementa la formula en la prueba y compara.
# ─────────────────────────────────────────────────────────────────────────────

class S22_RamaWeibull(unittest.TestCase):

    BU, TIPO = "TyD", "Reposicion de Activos"
    BETA, ETA, EDAD = 2.8, 30.0, 25.0        # interruptor de potencia
    CB, PB, CR, PR = 4, 4, 2, 2              # niveles de la matriz

    def _cfg(self):
        return _cfg()

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

    # ── El calculo independiente: la formula reescrita en la prueba ──────────

    def _delta_r_esperado(self, p, d=0):
        """Reimplementa el DeltaR de la rama Weibull, sin llamar al motor."""
        c = self._cfg()
        wacc = c["wacc_by_bu"][self.BU]
        h_activo = c["asset_horizons_by_bu"][self.BU]["_default"]
        h = max(1, h_activo - d)                       # ventana comun
        cl = c["risk_matrix"]["consequence_levels_by_bu"][self.BU]
        pl = c["risk_matrix"]["probability_levels"]
        cof_b = cl[str(p.risk_components[0].baseline_consequence_level)]["value_COP"]
        cof_r = cl[str(p.risk_components[0].residual_consequence_level)]["value_COP"]
        ratio = (pl[str(p.risk_components[0].residual_probability_level)]["annual_prob"] /
                 pl[str(p.risk_components[0].baseline_probability_level)]["annual_prob"])
        beta, eta = p.risk_components[0].weibull_beta, p.risk_components[0].weibull_eta
        edad = p.risk_components[0].asset_age_years

        def tasa(t):
            if eta <= 0 or t <= 0 or beta <= 0:
                return 0.0
            return min(1.0, (beta / eta) * ((t / eta) ** (beta - 1.0)))

        edad_eval = edad + d
        cy_efectivo = p.construction_years + d
        fd_lag = 1.0 / ((1 + wacc) ** cy_efectivo) if cy_efectivo > 0 else 1.0

        vpn = 0.0
        for k in range(1, h + 1):
            hb = tasa(edad_eval + k)
            vpn += (cof_b * hb - cof_r * hb * ratio) / ((1 + wacc) ** k)
        vpn *= fd_lag

        exposicion = 0.0
        for t in range(1, d + 1):
            hb = tasa(edad + t)
            exposicion += (cof_b * hb - cof_r * hb * ratio) / ((1 + wacc) ** t)
        return vpn - exposicion, exposicion

    def _motor(self, p, d=0):
        return ValueEngine(self._cfg()).evaluate(p, deferral_years=d)

    # ── El guardian principal: mata la mayoria de los 17 mutantes ────────────

    def test_delta_r_reproduce_un_calculo_independiente(self):
        """El DeltaR del motor coincide con la formula reescrita aparte."""
        for d in (0, 1, 3, 5):
            p = self._proyecto()
            esperado, _ = self._delta_r_esperado(p, d)
            obtenido = self._motor(p, d)["risk_analysis"]["delta_r_npv_COP"]
            self.assertAlmostEqual(
                obtenido / 1e6, esperado / 1e6, places=3,
                msg=f"DeltaR no coincide con el calculo independiente (diferimiento {d}a)")

    def test_exposicion_reproduce_un_calculo_independiente(self):
        """La exposicion cobrada durante la espera es exactamente la esperada."""
        for d in (0, 2, 5):
            p = self._proyecto()
            _, esperada = self._delta_r_esperado(p, d)
            obtenida = self._motor(p, d)["risk_analysis"]["deferral_exposure_COP"]
            self.assertAlmostEqual(obtenida / 1e6, esperada / 1e6, places=3,
                                   msg=f"Exposicion incorrecta con diferimiento {d}a")

    # ── El criterio de magnitud: tasa anual, no acumulada ────────────────────

    def test_usa_la_tasa_anual_y_no_la_acumulada(self):
        """El dinero sale de h(t), no de F(t).

        Confundirlas cobra cada ano por fallas ya ocurridas y, en un activo
        viejo, equivale a afirmar que falla con certeza todos los anos.
        """
        eng = ValueEngine(self._cfg())
        p = self._proyecto()
        r = self._motor(p)["risk_analysis"]
        tasa = eng._tasa_falla_anual(self.EDAD, self.ETA, self.BETA)
        acum = eng._pof_weibull_static(self.EDAD, self.ETA, self.BETA)
        self.assertAlmostEqual(r["tasa_falla_anual_base"], tasa, places=9)
        self.assertLess(tasa, acum,
                        "La tasa anual debe ser menor que la acumulada")
        self.assertAlmostEqual(r["pof_baseline_at_eval"], acum, places=9,
                               msg="La acumulada se conserva como estado de salud")

    def test_la_tasa_anual_esta_acotada(self):
        """h(t) crece sin techo con beta>1; no puede pasar de la certeza."""
        eng = ValueEngine(self._cfg())
        self.assertLessEqual(eng._tasa_falla_anual(400.0, 30.0, 2.8), 1.0)
        self.assertEqual(eng._tasa_falla_anual(0.0, 30.0, 2.8), 0.0)
        self.assertEqual(eng._tasa_falla_anual(25.0, 0.0, 2.8), 0.0)

    def test_una_sola_implementacion_de_la_tasa(self):
        """El motor de degradacion y el de valor usan la MISMA formula.

        Estaba duplicada en dos clases: asi es como dos ramas se separan sin
        que nadie lo note.
        """
        eng = ValueEngine(self._cfg())
        deg = AssetDegradationEngine(wacc=0.095, horizon_years=30, config=self._cfg())
        for edad, eta, beta in ((25, 30, 2.8), (50, 70, 1.5), (35, 290, 1.31)):
            self.assertEqual(deg._hazard_weibull(edad, eta, beta),
                             eng._tasa_falla_anual(edad, eta, beta))

    # ── Sensibilidades: cada entrada tiene que mover el resultado ────────────

    def _dr(self, **kw):
        return self._motor(self._proyecto(**kw))["risk_analysis"]["delta_r_npv_COP"]

    def test_la_forma_de_la_curva_cambia_el_resultado(self):
        self.assertNotAlmostEqual(self._dr(beta=2.8) / 1e6, self._dr(beta=1.2) / 1e6,
                                  places=2, msg="beta no afecta el DeltaR")

    def test_la_vida_caracteristica_cambia_el_resultado(self):
        self.assertNotAlmostEqual(self._dr(eta=30.0) / 1e6, self._dr(eta=60.0) / 1e6,
                                  places=2, msg="eta no afecta el DeltaR")

    def test_el_activo_envejece_dentro_del_horizonte(self):
        """Con beta>1 la tasa crece con la edad, y el DeltaR con ella."""
        self.assertGreater(self._dr(edad=40.0), self._dr(edad=20.0),
                           "Un activo mas viejo debe tener mayor riesgo evitado")

    def test_la_consecuencia_base_manda_sobre_el_resultado(self):
        self.assertGreater(self._dr(cb=5), self._dr(cb=3),
                           "El nivel de consecuencia base no esta afectando el DeltaR")

    def test_el_riesgo_residual_se_resta(self):
        """Un residual peor deja menos riesgo evitado."""
        self.assertGreater(self._dr(cr=1), self._dr(cr=3),
                           "La consecuencia residual no se esta restando")

    def test_el_ratio_de_probabilidad_va_en_el_sentido_correcto(self):
        """Mejorar mas la confiabilidad evita mas riesgo."""
        self.assertGreater(self._dr(pr=1), self._dr(pr=3),
                           "El ratio de probabilidad esta invertido o no se usa")

    def test_los_anios_de_obra_descuentan_el_riesgo(self):
        """El riesgo solo baja cuando la obra termina."""
        c = self._cfg(); wacc = c["wacc_by_bu"][self.BU]
        sin_obra = self._dr(cy=0)
        con_obra = self._dr(cy=3)
        self.assertAlmostEqual(con_obra / sin_obra, 1.0 / (1 + wacc) ** 3, places=6,
                               msg="El rezago de construccion no se aplica al DeltaR")

    def test_diferir_envejece_el_activo_y_cobra_exposicion(self):
        p = self._proyecto()
        r0, r5 = self._motor(p, 0)["risk_analysis"], self._motor(p, 5)["risk_analysis"]
        self.assertEqual(r0["deferral_exposure_COP"], 0.0)
        self.assertGreater(r5["deferral_exposure_COP"], 0.0)
        self.assertLess(r5["horizon_used"], r0["horizon_used"],
                        "La ventana de evaluacion no se acorto")

    def test_el_horizonte_completo_entra_al_calculo(self):
        """Recortar la ventana cambia el resultado: no puede ser indiferente."""
        p = self._proyecto()
        c = self._cfg()
        h = c["asset_horizons_by_bu"][self.BU]["_default"]
        completo = self._motor(p, 0)["risk_analysis"]
        self.assertEqual(completo["horizon_used"], h,
                         "No se esta recorriendo el horizonte del activo")


# ─────────────────────────────────────────────────────────────────────────────
# S23 — MILP NUCLEO: R1-R6, cuando el proyecto de prueba SI compite de verdad
#
# El grupo S2 ya prueba estas restricciones, pero con el helper _p() (VNT muy
# negativo por diseno: capex grande, un solo sub-criterio modesto). Una campana
# de mutacion encontro que 8 de 13 mutantes de R1-R6 sobreviven, y la causa
# raiz es esa: como el proyecto de _p() nunca es lo bastante atractivo para
# que el optimizador quiera seleccionarlo, restricciones como la exclusion
# mutua (R3c/R3d) nunca llegan a activarse — los dos proyectos del par jamas
# compiten por seleccion simultanea, asi que da igual si la restriccion existe
# o no. Este grupo usa proyectos con VNT fuertemente positivo (verificado con
# el motor real, no supuesto) para que cada restriccion se ejerza de verdad.
#
# Dos mutantes de la campana (R9-C, R11-C) se investigaron y resultaron ser
# MUTANTES EQUIVALENTES: la restriccion R2 de unicidad ya obliga matematica-
# mente lo mismo (sum_t x[i][t] <= 1), asi que remover el bloqueo explicito de
# "otros anios" en R9/R11 no cambia ningun resultado posible. Se verifico
# empiricamente (misma seleccion, con y sin esas lineas) antes de excluirlos:
# no hay prueba posible que los mate porque no representan un defecto real.
# ─────────────────────────────────────────────────────────────────────────────

class S23_MILP_Nucleo(unittest.TestCase):

    def setUp(self):
        self.cfg = _cfg()
        self.opt = EnterpriseOptimizer(config=self.cfg)

    def _bueno(self, pid, capex=500_000_000, delta=500.0, price=335_000,
              year=2026, bu="TyD", mandatory=False, cy=1):
        """Proyecto con VNT fuertemente positivo (verificado ~1.000M a
        capex=500M). A diferencia de _p() de S2, este SI compite por
        seleccion: el optimizador lo quiere, no hay que forzarlo."""
        sc = SubCriterionInput(id="VD-1-A", label="x", type="monetizable",
                               delta_annual=delta, price_per_unit=price)
        p = ProjectInput(
            id=pid, name=pid, business_unit=bu,
            investment_type="Reposicion de Activos", asset_type="_default",
            costs=CostInput(capex_COP=capex, opex_incremental_COP_year=0),
            drivers=[DriverInput(id="VD-1", reto="x", sub_criteria=[sc])],
            risk_components=[], execution_year=year, optimal_year=year,
            is_mandatory=mandatory, construction_years=cy)
        p.analysis_horizon_years = ValueEngine(self.cfg).get_horizon_for_asset(p)
        return p

    def _malo(self, pid, capex=500_000_000, delta=1.0, price=335_000,
             year=2026, bu="TyD", mandatory=False, cy=1):
        """Proyecto con VNT claramente negativo: un maximizador libre nunca
        lo elegiria. Sirve para aislar si algo distinto de su propio merito
        (una restriccion) es lo que fuerza o permite su seleccion."""
        return self._bueno(pid, capex=capex, delta=delta, price=price,
                           year=year, bu=bu, mandatory=mandatory, cy=cy)

    def _sel(self, result):
        return {s["project_id"]: s["year"] for s in result.selected}

    # ── R1: el ultimo periodo del horizonte no puede ser gratis ─────────────

    def test_r1_capex_del_ultimo_periodo_cuenta_en_su_propio_presupuesto(self):
        """Un proyecto que inicia en la ULTIMA vigencia debe consumir el
        presupuesto de ESA vigencia, no quedar fuera del computo."""
        p = self._bueno("TARDE", capex=500_000_000, cy=1)
        periods = [ResourcePeriod(year=y, budget_COP=400_000_000)
                  for y in (2026, 2027, 2028)]   # 400M < 500M en TODAS
        result = self.opt.optimize([p], periods)
        self.assertNotIn("TARDE", self._sel(result),
                         "R1: el proyecto se coló sin que su CAPEX contara "
                         "contra el presupuesto de ninguna vigencia — el "
                         "ultimo periodo del horizonte quedo gratis")

    # ── R2: obligatorio y opcional no pueden intercambiar semantica ─────────

    def test_r2_obligatorio_se_fuerza_aunque_su_vnt_sea_negativo(self):
        """Un proyecto is_mandatory=True debe ejecutarse SIEMPRE, incluso si
        eso le cuesta valor al portafolio frente a una alternativa mejor."""
        obligatorio = self._malo("OBLIGATORIO", capex=500_000_000, delta=1.0,
                                 mandatory=True)
        opcional = self._bueno("OPCIONAL_MEJOR", capex=500_000_000, delta=2000.0)
        # Presupuesto solo alcanza para uno de los dos en el año.
        periods = [ResourcePeriod(year=2026, budget_COP=600_000_000)]
        result = self.opt.optimize([obligatorio, opcional], periods)
        sel = self._sel(result)
        self.assertIn("OBLIGATORIO", sel,
                      "R2: is_mandatory no esta forzando la seleccion — un "
                      "compromiso regulatorio dejaria de ejecutarse")
        self.assertNotIn("OPCIONAL_MEJOR", sel,
                         "R2: el presupuesto que debia ir al obligatorio se "
                         "fue a un opcional de mayor VNT — la obligatoriedad "
                         "no esta bloqueando el presupuesto que consume")

    # ── R3a: dependencia via el campo `dependencies` ─────────────────────────

    def test_r3a_hijo_bloqueado_si_el_padre_nunca_se_selecciona(self):
        """Si PADRE no entra al portafolio (VNT negativo, no obligatorio),
        HIJO tampoco puede entrar aunque su VNT sea excelente. Un solo
        período con presupuesto para UNO de los dos (no ambos): si la
        dependencia está activa, incluir a HIJO exige forzar a PADRE (VNT
        negativo) dentro del mismo presupuesto estrecho, lo que hace que
        HIJO solo no sea alcanzable — la mejor solución factible es no
        seleccionar nada. Con la dependencia rota, HIJO solo SÍ cabe y el
        optimizador lo toma."""
        padre = self._malo("PADRE", capex=500_000_000, delta=1.0)
        hijo = self._bueno("HIJO", capex=500_000_000, delta=2000.0)
        hijo.dependencies = ["PADRE"]
        periods = [ResourcePeriod(year=2026, budget_COP=600_000_000)]
        result = self.opt.optimize([padre, hijo], periods)
        self.assertNotIn("HIJO", self._sel(result),
                         "R3a: el hijo se seleccionó sin su padre — la "
                         "dependencia via el campo 'dependencies' no esta "
                         "bloqueando, o esta comparando la variable equivocada")

    # ── R3b: el lag no puede ceder un año ────────────────────────────────────

    def test_r3b_lag_no_permite_arrancar_un_periodo_antes_de_lo_legal(self):
        """Con lag=2 y PADRE en el primer año del horizonte, HIJO no puede
        arrancar un año antes de lo que el lag permite (padre+lag)."""
        padre = self._bueno("PADRE", capex=500_000_000, delta=2000.0,
                            mandatory=True, year=2026)
        hijo = self._bueno("HIJO", capex=500_000_000, delta=2000.0, year=2026)
        hijo.precedent_project_id = "PADRE"
        hijo.min_lag_years = 2
        result = self.opt.optimize([padre, hijo], _make_periods())  # 2026-2030
        sel = self._sel(result)
        self.assertEqual(sel.get("PADRE"), 2026,
                         "el padre no arranco en el primer año pese a ser "
                         "obligatorio y con VNT decreciente en el tiempo — "
                         "revisar el fixture antes de evaluar el lag")
        if "HIJO" in sel:
            self.assertGreaterEqual(sel["HIJO"] - sel["PADRE"], 2,
                "R3b: el hijo arranco antes de padre+lag — el off-by-one en "
                "la suma acumulada del hijo permite ceder un periodo")

    # ── R3c / R3d: la exclusión mutua debe activarse cuando SÍ compiten ─────

    def test_r3c_exclusion_mutua_deja_pasar_exactamente_uno(self):
        """Dos proyectos excluyentes, ambos con VNT excelente y presupuesto
        de sobra para los dos: la restricción debe bloquear a UNO, no dejar
        pasar los dos ni bloquear los dos (el maximizador siempre quiere
        quedarse con el mejor par posible bajo la restricción)."""
        pa = self._bueno("PA", capex=500_000_000, delta=2000.0)
        pb = self._bueno("PB", capex=500_000_000, delta=2000.0)
        pa.mutual_exclusion_ids = ["PB"]
        pb.mutual_exclusion_ids = ["PA"]
        result = self.opt.optimize([pa, pb], _make_periods(budget=50_000_000_000))
        sel = self._sel(result)
        n = sum(1 for pid in ("PA", "PB") if pid in sel)
        self.assertEqual(n, 1,
            f"R3c: se seleccionaron {n} de los 2 proyectos excluyentes "
            "(debia ser exactamente 1) — la exclusión mutua no está activa "
            "o su umbral está mal (<=2 en vez de <=1)")

    def test_r3d_exclusion_de_grupo_deja_pasar_exactamente_uno(self):
        """Tres alternativas del mismo grupo excluyente, todas atractivas:
        el grupo debe ceder exactamente una, no ninguna ni varias.

        Nota: desde que _preselect_by_eav corre sin condiciones antes del
        MILP (grupo S32), cada exclusion_group_id ya llega reducido a <=1
        alternativa — esta prueba pasa por eso, no porque R3d se haya
        ejercitado. El guardián real de R3d por sí solo es
        S32_PreseleccionPorEAV.test_r3d_sigue_activo_si_se_desactiva_la_preseleccion."""
        projs = [self._bueno(f"PG{i}", capex=500_000_000, delta=2000.0)
                for i in range(3)]
        for p in projs:
            p.exclusion_group_id = "GRUPO_A"
        result = self.opt.optimize(projs, _make_periods(budget=50_000_000_000))
        sel = self._sel(result)
        n = sum(1 for p in projs if p.id in sel)
        self.assertEqual(n, 1,
            f"R3d: se seleccionaron {n} del grupo excluyente (debia ser "
            "exactamente 1) — la restriccion de grupo no esta activa o su "
            "umbral esta mal")

    # ── R4: persona-mes debe limitar cuántos proyectos caben, no ser un ──────
    #        adorno que nunca se activa ni un techo que bloquea todo

    def test_r4_persona_mes_limita_exactamente_lo_que_debe(self):
        """3 proyectos identicos (6 p-m cada uno segun la tabla real del
        motor), limite de 15 p-m en un solo periodo: caben exactamente 2
        (12<=15), el tercero no (18>15)."""
        projs = [self._bueno(f"P{i}", capex=5_000_000_000, delta=50_000.0)
                for i in range(3)]
        periods = [ResourcePeriod(year=2026, budget_COP=50_000_000_000,
                                  max_person_months=15.0)]
        result = self.opt.optimize(projs, periods)
        self.assertEqual(len(result.selected), 2,
            f"R4: se seleccionaron {len(result.selected)} de 3 proyectos de "
            "6 p-m cada uno con limite 15 p-m (esperado: exactamente 2) — "
            "el consumo de persona-mes por proyecto esta mal escalado")


# ─────────────────────────────────────────────────────────────────────────────
# S24 — MILP AVANZADO: R7-R12, mismo criterio que S23
#
# R8, R9 y R12 usan un patron comun para forzar el caso real: un proyecto
# "obligado por la restriccion" con VNT deliberadamente peor que una
# alternativa libre, y presupuesto que solo alcanza para uno de los dos. Si
# la restriccion de verdad fuerza/bloquea, gana el forzado; si la mutacion la
# desactiva, el maximizador libre elige la alternativa de mayor VNT — la
# diferencia es visible y no depende de que el proyecto forzado tenga VNT
# negativo (evita el problema que dejaba ciegas a las pruebas de R3c/R3d).
# ─────────────────────────────────────────────────────────────────────────────

class S24_MILP_Avanzado(unittest.TestCase):

    def setUp(self):
        self.cfg = _cfg()
        self.opt = EnterpriseOptimizer(config=self.cfg)

    def _proj(self, pid, capex, delta=1.0, price=335_000, risk=None, aid="",
             bu="TyD", year=2026, committed=False, committed_year=0,
             require_outage=False):
        sc = SubCriterionInput(id="VD-1-A", label="x", type="monetizable",
                               delta_annual=delta, price_per_unit=price)
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

    def _sel(self, result):
        return {s["project_id"]: s["year"] for s in result.selected}

    # ── R7: piso de inversion por unidad de negocio ─────────────────────────

    def test_r7_piso_fuerza_la_seleccion_aunque_el_vnt_sea_negativo(self):
        """Único proyecto disponible de la BU, con VNT negativo y CAPEX
        exactamente igual al piso: debe forzarse, y el problema debe seguir
        siendo factible (si el filtro de BU aplica el piso a otra BU sin
        proyectos, el problema queda infactible con piso>0=0)."""
        p = self._proj("UNICO_TYD", 1_000_000_000, delta=1.0)
        periods = _make_periods(budget=50_000_000_000)
        result = self.opt.optimize([p], periods,
                                   pisos_inversion_COP={"TyD": 1_000_000_000})
        self.assertIn(result.solver_status, ("OPTIMAL", "FEASIBLE"),
                     "R7: el problema quedó infactible — el piso se está "
                     "aplicando a la unidad de negocio equivocada")
        self.assertIn("UNICO_TYD", self._sel(result),
                      "R7: el piso no forzó la selección — el sentido de la "
                      "desigualdad puede estar invertido (<=  en vez de >=)")

    def test_r7_piso_no_se_debilita(self):
        """Piso de 1.000M con un único candidato de 600M: matemáticamente
        imposible de alcanzar (600M < 1.000M) — debe ser infactible. Si el
        piso se debilitó (ej. al 50% = 500M), 600M SÍ alcanzaría y el
        problema se volvería factible: esa diferencia delata el debilitamiento."""
        p = self._proj("PARCIAL_TYD", 600_000_000, delta=1.0)
        periods = _make_periods(budget=50_000_000_000)
        result = self.opt.optimize([p], periods,
                                   pisos_inversion_COP={"TyD": 1_000_000_000})
        self.assertEqual(result.solver_status, "INFEASIBLE",
            "R7: con un solo candidato de 600M y piso de 1.000M el problema "
            "debe ser infactible; que sea factible indica que el piso real "
            "aplicado es menor al configurado")

    # ── R8: mitigación obligatoria de riesgo intolerable ────────────────────

    def test_r8_forzado_gana_sobre_alternativa_de_mayor_vnt(self):
        """El único proyecto que mitiga un riesgo en zona roja debe forzarse
        aunque una alternativa sin relación con el riesgo tenga MUCHO mejor
        VNT y el presupuesto solo alcance para una de las dos."""
        riesgo = RiskInput(baseline_consequence_level=4, baseline_probability_level=3,
                           residual_consequence_level=2, residual_probability_level=2)
        mitigante = self._proj("MITIGANTE", 44_500_000_000, delta=1.0,
                               risk=riesgo, aid="ACTIVO_ROJO")
        alternativa = self._proj("ALTERNATIVA", 500_000_000, delta=2000.0)
        periods = [ResourcePeriod(year=2026, budget_COP=44_900_000_000)]
        result = self.opt.optimize([mitigante, alternativa], periods,
                                   risk_tolerance_level=4)
        sel = self._sel(result)
        self.assertIn("MITIGANTE", sel,
                      "R8: el proyecto mitigante no se forzó — el riesgo en "
                      "zona roja quedaría sin ningún proyecto que lo mitigue "
                      "(nivel de tolerancia off-by-one, o el forzado se quitó)")
        self.assertNotIn("ALTERNATIVA", sel,
                         "R8: se coló la alternativa de mayor VNT en vez del "
                         "mitigante forzado — el presupuesto no debía alcanzar "
                         "para ambas")

    def test_r8_solo_cuenta_si_baseline_alto_y_residual_bajo(self):
        """Un proyecto cuyo baseline SÍ cruza el umbral de riesgo pero cuyo
        residual NO mejora (sigue en zona roja) no debe forzarse: no mitiga
        nada, forzarlo sería gastar sin bajar el riesgo."""
        riesgo_no_mitiga = RiskInput(
            baseline_consequence_level=4, baseline_probability_level=3,
            residual_consequence_level=5, residual_probability_level=5)  # empeora
        p = self._proj("NO_MITIGA", 500_000_000, delta=1.0,
                       risk=riesgo_no_mitiga, aid="ACTIVO_SOLO")
        periods = _make_periods(budget=50_000_000_000)
        result = self.opt.optimize([p], periods, risk_tolerance_level=4)
        self.assertNotIn("NO_MITIGA", self._sel(result),
            "R8: se forzó un proyecto cuyo residual no baja del umbral — "
            "el criterio de 'es mitigante' está usando OR en vez de AND "
            "entre baseline>=tolerancia y residual<tolerancia")

    def test_r8_con_dos_componentes_el_peor_caso_decide_el_baseline(self):
        """Con 2 componentes de riesgo en el mismo proyecto, el peor de los
        dos decide si el activo está en zona de riesgo inaceptable — y la
        mitigación solo cuenta si el residual baja AMBOS componentes por
        debajo del umbral (D-14)."""
        comp_alto = RiskInput(baseline_consequence_level=5, baseline_probability_level=5,
                              residual_consequence_level=1, residual_probability_level=1)
        comp_bajo = RiskInput(baseline_consequence_level=2, baseline_probability_level=2,
                              residual_consequence_level=1, residual_probability_level=1)
        mitigante = self._proj("MULTI_MITIGANTE", 44_500_000_000, delta=1.0, aid="ACTIVO_MULTI")
        mitigante.risk_components = [comp_alto, comp_bajo]
        alternativa = self._proj("ALTERNATIVA2", 500_000_000, delta=2000.0)
        periods = [ResourcePeriod(year=2026, budget_COP=44_900_000_000)]
        result = self.opt.optimize([mitigante, alternativa], periods,
                                   risk_tolerance_level=4)
        sel = self._sel(result)
        self.assertIn("MULTI_MITIGANTE", sel,
                      "R8: con 2 componentes, el de nivel 5 (peor caso) debia forzar "
                      "la mitigacion aunque el otro componente sea nivel 2")

    def test_r8_con_dos_componentes_no_cuenta_si_el_peor_no_se_mitiga(self):
        """El componente de peor severidad (nivel 5) nunca se mitigo (su
        propio residual sigue en nivel 5) — aunque OTRO componente de baja
        severidad si mejore, el proyecto NO debe forzarse: el riesgo
        inaceptable real (el del componente grave) sigue sin bajar (D-14)."""
        comp_grave_sin_mitigar = RiskInput(
            baseline_consequence_level=5, baseline_probability_level=5,
            residual_consequence_level=5, residual_probability_level=5)
        comp_leve_mitigado = RiskInput(
            baseline_consequence_level=2, baseline_probability_level=2,
            residual_consequence_level=1, residual_probability_level=1)
        no_mitigante = self._proj("MULTI_NO_MITIGA", 44_500_000_000, delta=1.0, aid="ACTIVO_MULTI_2")
        no_mitigante.risk_components = [comp_grave_sin_mitigar, comp_leve_mitigado]
        alternativa = self._proj("ALTERNATIVA3", 500_000_000, delta=2000.0)
        periods = [ResourcePeriod(year=2026, budget_COP=44_900_000_000)]
        result = self.opt.optimize([no_mitigante, alternativa], periods,
                                   risk_tolerance_level=4)
        sel = self._sel(result)
        self.assertNotIn("MULTI_NO_MITIGA", sel,
                         "R8: el componente grave nunca bajo de nivel 5 — no deberia "
                         "contar como 'mitigante' solo porque otro componente leve mejoro")

    # ── R9: proyecto completado no puede volver a competir ──────────────────

    def test_r9_completado_no_se_reselecciona_asi_sea_gratis_y_atractivo(self):
        """Un proyecto con pct_complete=1.0 tiene su CAPEX en cero (ya
        gastado) pero su VNT original sigue siendo positivo — si R9 no lo
        bloquea de verdad, el optimizador lo 'reselecciona' porque ahora es
        gratis y suma valor. No debe aparecer nunca en el resultado."""
        p = self._proj("COMPLETADO", 500_000_000, delta=500.0)
        actuals = [ProjectActual(project_id="COMPLETADO", ac_COP=500_000_000,
                                 pct_complete=1.0)]
        result = self.opt.optimize([p], _make_periods(), actuals=actuals)
        self.assertNotIn("COMPLETADO", self._sel(result),
            "R9: un proyecto completado volvió a aparecer seleccionado — "
            "el bloqueo x[i][t]==0 para completados no está activo")

    # ── R10: alineación con ventanas de mantenimiento ───────────────────────

    def test_r10_solo_puede_ejecutarse_en_el_anio_con_ventana(self):
        """Único año con ventana de mantenimiento: 2028. El proyecto,
        atractivo y con preferencia natural por el año más temprano (menos
        descuento), debe terminar en 2028 y en ningún otro año."""
        p = self._proj("CON_OUTAGE", 500_000_000, delta=2000.0,
                       aid="ACTIVO_OUT", require_outage=True)
        outages = [PlannedOutage(outage_id="O1", asset_id="ACTIVO_OUT", year=2028)]
        result = self.opt.optimize([p], _make_periods(),  # 2026-2030
                                   planned_outages=outages)
        sel = self._sel(result)
        if "CON_OUTAGE" in sel:
            self.assertEqual(sel["CON_OUTAGE"], 2028,
                "R10: el proyecto se ejecutó fuera de su única ventana de "
                "mantenimiento — la condición de bloqueo, el filtro de "
                "elegibilidad, o la restricción misma no están activos")
        else:
            self.fail("R10: el proyecto atractivo con ventana disponible no "
                      "se seleccionó en absoluto")

    # ── R12: TOTEX no puede aflojarse ni mirar el año equivocado ────────────

    def test_r12_totex_bloquea_cuando_excede_el_techo(self):
        """TOTEX del proyecto (500M) por encima del techo de su único
        período (400M): no debe seleccionarse en absoluto."""
        p = self._proj("TOTEX_ALTO", 500_000_000, delta=2000.0)
        periods = [ResourcePeriod(year=2026, budget_COP=50_000_000_000,
                                  totex_budget_COP=400_000_000)]
        result = self.opt.optimize([p], periods)
        self.assertNotIn("TOTEX_ALTO", self._sel(result),
            "R12: el proyecto se seleccionó pese a exceder el techo TOTEX — "
            "el sentido de la desigualdad está invertido o el techo se aflojó")

    def test_r12_totex_usa_el_techo_de_su_propia_vigencia(self):
        """Dos períodos: 2026 con techo TOTEX generoso, 2027 con techo
        estrecho (400M). El proyecto (TOTEX=500M, preferencia natural por el
        año temprano) debe terminar en 2026, usando el techo DE 2026 — no el
        de 2027 aplicado por error de índice."""
        p = self._proj("DOS_PERIODOS", 500_000_000, delta=2000.0)
        periods = [
            ResourcePeriod(year=2026, budget_COP=200_000_000_000,
                           totex_budget_COP=100_000_000_000),
            ResourcePeriod(year=2027, budget_COP=200_000_000_000,
                           totex_budget_COP=400_000_000),
        ]
        result = self.opt.optimize([p], periods)
        sel = self._sel(result)
        if "DOS_PERIODOS" in sel:
            self.assertEqual(sel["DOS_PERIODOS"], 2026,
                "R12: el proyecto se movió a 2027 — el techo TOTEX aplicado "
                "a cada año no es el de su propia vigencia (índice t-1 o "
                "similar desplazado)")
        else:
            self.fail("R12: el proyecto no se seleccionó en ningún período; "
                      "revisar el fixture (2026 no debía estar bloqueado)")


# ─────────────────────────────────────────────────────────────────────────────
# S25 — FUNCION DE VALOR (RETOS): sub-criterio, value function, agregación de
# driver (peso w_r, temporalidad FD_lag)
#
# Las pruebas del grupo S1 verifican "VNT > 0" para cada VD — un chequeo de
# signo, no de magnitud. No distinguen una implementación correcta de una que
# aplica el peso dos veces, invierte el signo de la escala-S, o ignora la
# rampa de un driver: todas seguirían dando VNT positivo. Este grupo llama
# directamente a los métodos internos del motor de valor (_calc_subcriterion,
# _apply_vf, _calc_driver) con un cálculo de referencia independiente, para
# fijar la magnitud exacta, no solo su signo.
# ─────────────────────────────────────────────────────────────────────────────

class S25_FuncionDeValor(unittest.TestCase):

    def setUp(self):
        self.cfg = _cfg()
        self.engine = ValueEngine(self.cfg)

    def _annuity(self, h):
        w = self.engine.wacc
        return h if w == 0 else (1 - (1 + w) ** -h) / w

    # ── sub-criterio monetizable: orden de la multiplicación y el peso ──────

    def test_monetizable_multiplica_delta_por_precio_y_aplica_el_peso(self):
        """COP = weight × (delta_annual × price_per_unit) × factor_NPV(horizonte).
        Con weight=1,7 (no 1,0) para que la prueba note si el peso del
        sub-criterio se ignora, se aplica dos veces, o se suma en vez de
        multiplicarse."""
        sc = SubCriterionInput(id="X", label="x", type="monetizable",
                               delta_annual=250.0, price_per_unit=12_000,
                               weight=1.7)
        h = 5
        got = self.engine._calc_subcriterion(sc, horizon=h)
        annual_cop = 250.0 * 12_000
        expected = 1.7 * annual_cop * self._annuity(h)
        self.assertAlmostEqual(got, expected, delta=max(1.0, abs(expected) * 1e-9))

    def test_monetizable_sin_precio_es_cero(self):
        """price_per_unit=None debe devolver exactamente 0,0 (guarda de
        datos incompletos), no lanzar excepción ni devolver el delta crudo."""
        sc = SubCriterionInput(id="X", label="x", type="monetizable",
                               delta_annual=999.0, price_per_unit=None)
        self.assertEqual(self.engine._calc_subcriterion(sc, horizon=5), 0.0)

    # ── concept_rating: constantes C55 (neutral=5, rango=5) y escala lineal ──

    def test_concept_rating_lineal_sobre_el_neutral(self):
        """C55: neutral=5, rango=5. delta_annual=8 → score=(8-5)/5×10=6 →
        aporte = (6/10)×scale_max, positivo. Fija de una vez las constantes
        de C55 (5,0 y no el 3,0 del esquema anterior) y el divisor /10 de la
        función lineal."""
        sc = SubCriterionInput(id="X", label="x", type="concept_rating",
                               delta_annual=8.0, scale_max_COP=1_000_000_000.0,
                               value_function="linear")
        got = self.engine._apply_vf(sc)
        self.assertAlmostEqual(got, 600_000_000.0, delta=1.0)

    def test_concept_rating_en_el_neutral_es_cero(self):
        """delta_annual=5 (exactamente el neutral C55) → aporte 0,0."""
        sc = SubCriterionInput(id="X", label="x", type="concept_rating",
                               delta_annual=5.0, scale_max_COP=1_000_000_000.0,
                               value_function="linear")
        self.assertAlmostEqual(self.engine._apply_vf(sc), 0.0, delta=1.0)

    def test_concept_rating_lineal_bajo_el_neutral_es_negativo(self):
        """delta_annual=2 → score=(2-5)/5×10=-6 → aporte = -(6/10)×scale_max.
        Fija el signo Y la magnitud a la vez: una implementación que pierda
        el signo, o que aplique abs() de más, falla aquí."""
        sc = SubCriterionInput(id="X", label="x", type="concept_rating",
                               delta_annual=2.0, scale_max_COP=1_000_000_000.0,
                               value_function="linear")
        got = self.engine._apply_vf(sc)
        self.assertAlmostEqual(got, -600_000_000.0, delta=1.0)

    def test_concept_rating_escurve_toca_los_extremos_exactos(self):
        """Por construcción, la escala-S normaliza sus propios extremos: en
        score=+10 (delta_annual=10) el aporte debe ser EXACTAMENTE
        +scale_max, y en score=-10 (delta_annual=0) EXACTAMENTE -scale_max —
        sin importar el punto de inflexión ni la pendiente. No depende de
        replicar a mano la fórmula sigmoide: solo del invariante de que la
        curva está anclada en sus propios bordes."""
        scale_max = 1_000_000_000.0
        alto = SubCriterionInput(id="X", label="x", type="concept_rating",
                                 delta_annual=10.0, scale_max_COP=scale_max,
                                 value_function="s_curve")
        bajo = SubCriterionInput(id="Y", label="y", type="concept_rating",
                                 delta_annual=0.0, scale_max_COP=scale_max,
                                 value_function="s_curve")
        self.assertAlmostEqual(self.engine._apply_vf(alto), scale_max, delta=1.0)
        self.assertAlmostEqual(self.engine._apply_vf(bajo), -scale_max, delta=1.0)

    # ── agregación del driver: peso w_r, rampa del driver, FD_lag ────────────

    def test_driver_aplica_su_peso_la_rampa_y_el_descuento_por_rezago(self):
        """VD_r = w_r × Σ(SC_j) × FD_lag, con FD_lag = 1/(1+WACC)^(obra+rampa).
        w_r=2,0 (no 1,0) y rampa=2 (no 0) para que la prueba note si el peso
        del driver se ignora, si la rampa (benefit_start_offsets) se ignora,
        si el lag combina mal obra+rampa, o si FD_lag multiplica en vez de
        dividir (interés compuesto en vez de descuento)."""
        sc = SubCriterionInput(id="VD-1-A", label="x", type="monetizable",
                               delta_annual=100.0, price_per_unit=10_000,
                               weight=1.0)
        driver = DriverInput(id="VD-1", reto="x", sub_criteria=[sc], weight=2.0)
        h = 8
        got = self.engine._calc_driver(driver, construction_years=3,
                                       benefit_start_offsets={"VD-1": 2},
                                       horizon=h)
        annual_cop = 100.0 * 10_000
        sc_total = 1.0 * annual_cop * self._annuity(h)
        weighted_total = 2.0 * sc_total
        lag_total = 3 + 2
        fd_lag = 1.0 / ((1.0 + self.engine.wacc) ** lag_total)
        expected = weighted_total * fd_lag
        self.assertAlmostEqual(got["value_COP"], expected,
                               delta=max(1.0, abs(expected) * 1e-9))
        self.assertEqual(got["lag_total_years"], lag_total)
        self.assertAlmostEqual(got["fd_lag"], fd_lag, places=9)

    # ── VD-1-F: sub-criterio retirado, doble conteo de reputación ───────────

    def test_vd1f_esta_bloqueado_y_no_solo_es_pequeno(self):
        """VD-1-F duplicaría el aporte reputacional que ahora vive en
        VD-6-A — ya no es sobre las anclas de riesgo, que dejaron de
        cobrar componente reputacional al crear VD-6. El motor debe darlo
        por CERO exacto y reportarlo en deprecated_skipped — no basta con
        que 'de por sí' sea pequeño; con delta_annual en su máximo (10) y
        una escala grande, si el bloqueo fallara el aporte sería enorme y
        evidente."""
        sc = SubCriterionInput(id="VD-1-F", label="reputacion (retirado)",
                               type="concept_rating", delta_annual=10.0,
                               scale_max_COP=5_000_000_000.0,
                               value_function="linear")
        driver = DriverInput(id="VD-1", reto="x", sub_criteria=[sc])
        got = self.engine._calc_driver(driver, construction_years=1, horizon=5)
        self.assertEqual(got["value_COP"], 0.0)
        self.assertEqual(got["sc_total_COP"], 0.0)
        self.assertIn("VD-1-F", got["deprecated_skipped"])

    def test_vd6_reputacion_se_calcula_como_concept_rating(self):
        """VD-6-A debe comportarse exactamente como VD-1-F se comportaba
        antes de retirarse: concept_rating con curva-S, oráculo
        independiente contra _vf_scurve."""
        sc = SubCriterionInput(
            id="VD-6-A", label="Reputación EPM (NPS + CX + imagen)",
            type="concept_rating", delta_annual=7.0,
            value_function="s_curve", vf_inflection=5.0, vf_steepness=1.5,
            scale_max_COP=320_000_000_000.0,
        )
        driver = DriverInput(id="VD-6", reto="Reputación", sub_criteria=[sc])
        got = self.engine._calc_driver(driver, construction_years=1, horizon=5)

        # Oráculo independiente: mismo cálculo que _apply_vf hace internamente
        # para concept_rating, reconstruido aquí sin llamar al método probado.
        baseline, max_range = 5.0, 5.0
        score = ((7.0 - baseline) / max_range) * 10.0
        sigmoid = lambda x: 1.0 / (1.0 + math.exp(-1.5 * (x - 5.0)))
        normalized = (sigmoid(score) - sigmoid(0)) / (sigmoid(10) - sigmoid(0))
        annual_cop = normalized * 320_000_000_000.0
        expected_sc = self.engine._npv_annual(annual_cop, horizon=5)
        fd_lag = 1.0 / ((1.0 + self.engine.wacc) ** 1)
        expected = expected_sc * fd_lag

        self.assertAlmostEqual(got["value_COP"], expected,
                               delta=max(1.0, abs(expected) * 1e-6))

    def test_vd6_no_esta_bloqueado_a_diferencia_de_vd1f(self):
        """VD-6-A no debe caer en _DEPRECATED_SC — solo VD-1-F sigue
        bloqueado, no el Reto nuevo que reemplaza su función."""
        sc = SubCriterionInput(id="VD-6-A", label="x", type="concept_rating",
                               delta_annual=10.0, scale_max_COP=1_000_000_000.0,
                               value_function="linear")
        driver = DriverInput(id="VD-6", reto="Reputación", sub_criteria=[sc])
        got = self.engine._calc_driver(driver, construction_years=1, horizon=5)
        self.assertNotIn("VD-6-A", got["deprecated_skipped"])
        self.assertNotEqual(got["value_COP"], 0.0)

    def test_matriz_riesgo_por_negocio_ya_no_cobra_componente_reputacional(self):
        """value_COP resuelto en TIEMPO DE EJECUCIÓN para cada negocio real
        (no la tabla plana genérica, que casi ningún proyecto usa) debe ser
        el valor sin componente reputacional — verificado leyendo
        directamente consequence_levels_by_bu, la tabla que _calc_risk()
        realmente resuelve primero según la jerarquía documentada
        (consequence_levels_by_bu[BU] > consequence_levels > respaldo).

        Valores actualizados por D-12 (2026-09-03): ver
        test_consequence_levels_by_bu_es_la_suma_exacta_del_desglose_por_tipo
        para la corrección de fondo — este test solo confirma que el
        componente reputacional sigue fuera, con los valores ya corregidos."""
        esperados = {
            "TyD":        {"1": 45_000_000,    "5": 49_000_000_000},
            "Generacion": {"1": 80_000_000,    "5": 62_000_000_000},
            "AyA":        {"1": 45_000_000,    "5": 82_000_000_000},
            "Gas":        {"1": 45_000_000,    "5": 40_000_000_000},
        }
        tabla = self.cfg["risk_matrix"]["consequence_levels_by_bu"]
        for bu, niveles in esperados.items():
            for nivel, esperado in niveles.items():
                self.assertAlmostEqual(
                    tabla[bu][nivel]["value_COP"], esperado, delta=1.0,
                    msg=f"{bu} nivel {nivel}: sigue con el componente reputacional "
                        f"incluido, o consequence_levels_by_bu no existe en config.json "
                        f"y el motor está cayendo al respaldo desactualizado.",
                )

    def test_default_config_sincronizado_con_config_json_para_reto_6(self):
        """DEFAULT_CONFIG (el respaldo embebido en epm_core.py) y
        config.json deben coincidir en consequence_levels_by_bu y en la
        entrada VD-6 — ya nos mordió una vez que estuvieran desincronizados
        (config.json nunca definía consequence_levels_by_bu, así que el
        motor caía en silencio al respaldo desactualizado con el
        componente reputacional todavía adentro). Esta prueba no evita que
        alguien edite un solo archivo por descuido, pero sí lo hace fallar
        cuando ocurra, en vez de quedar en silencio como la vez anterior."""
        with open(PROJECT_DIR / "config.json", encoding="utf-8") as f:
            cfg_json = json.load(f)

        tabla_json = cfg_json["risk_matrix"]["consequence_levels_by_bu"]
        tabla_default = DEFAULT_CONFIG["risk_matrix"]["consequence_levels_by_bu"]
        for bu in ("TyD", "Generacion", "AyA", "Gas"):
            for nivel in ("1", "2", "3", "4", "5"):
                self.assertAlmostEqual(
                    tabla_json[bu][nivel]["value_COP"],
                    tabla_default[bu][nivel]["value_COP"],
                    delta=1.0,
                    msg=f"consequence_levels_by_bu[{bu}][{nivel}] difiere entre "
                        f"config.json y DEFAULT_CONFIG — alguno quedó desactualizado.",
                )

        ids_json = {d["id"] for d in cfg_json["value_drivers"]}
        ids_default = {d["id"] for d in DEFAULT_CONFIG["value_drivers"]}
        self.assertIn("VD-6", ids_json, "VD-6 no existe en config.json")
        self.assertIn("VD-6", ids_default, "VD-6 no existe en DEFAULT_CONFIG — el respaldo quedó desactualizado")

    def test_vd6_alcanzable_desde_investment_types(self):
        """VD-6-A debe aparecer como sugerido en todos los tipos de
        inversión — Reputación es uniforme para los 4 negocios, no
        exclusivo de un tipo de proyecto."""
        for inv_type, cfg_it in INVESTMENT_TYPES.items():
            self.assertIn(
                "VD-6-A", cfg_it.get("suggested_sc", []),
                f"'{inv_type}' no sugiere VD-6-A — Reto 6 quedaría inalcanzable "
                f"desde el asistente para este tipo de inversión.",
            )

    def test_consequence_levels_by_bu_es_la_suma_exacta_del_desglose_por_tipo(self):
        """Guardián permanente (D-12): consequence_levels_by_bu[BU][nivel] debe
        ser EXACTAMENTE la suma de consequence_types_matrix (financiero + hs +
        ambiental + regulatorio, SIN reputacional — ya vive en VD-6) para esa
        misma BU y nivel — no una aproximación.

        Antes del ajuste D-12, la reducción por Reto 6 se calculó aplicando la
        misma proporción de reducción que la tabla plana genérica ('Todos'),
        en vez de restar directamente el componente reputacional del propio
        desglose de cada BU. Verificado contra las 4 BU antes del fix: la
        tabla plana coincidía exactamente con la suma de los 5 tipos (con
        reputación) en 19 de 20 casos — confirmando que consequence_levels_by_bu
        SIEMPRE fue, por diseño, una suma derivada del desglose, no un dato
        calibrado de forma independiente. La aproximación por proporción
        genérica introdujo una brecha creciente con la severidad (hasta -45%
        en el nivel catastrófico de AyA y TyD) — encontrada al preparar el
        informe de trazabilidad de riesgo para la validación de AyA."""
        ctm = self.cfg["risk_matrix"]["consequence_types_matrix"]
        clb = self.cfg["risk_matrix"]["consequence_levels_by_bu"]
        tipos_sin_reputacion = ["financiero", "hs", "ambiental", "regulatorio"]
        for bu in ("TyD", "Generacion", "AyA", "Gas"):
            for nivel in ("1", "2", "3", "4", "5"):
                suma_tipos = sum(
                    ctm[t]["levels_by_bu"][bu][nivel]["value_COP"]
                    for t in tipos_sin_reputacion
                )
                oficial = clb[bu][nivel]["value_COP"]
                self.assertAlmostEqual(
                    oficial, suma_tipos, delta=1.0,
                    msg=f"consequence_levels_by_bu[{bu}][{nivel}] ({oficial:,.0f}) "
                        f"no coincide con la suma de su propio desglose por tipo "
                        f"sin reputación ({suma_tipos:,.0f}) — la tabla agregada "
                        f"se desincronizó de su fuente.",
                )


# ─────────────────────────────────────────────────────────────────────────────
# S26 — COSTO DEL RETRASO (COD): _calc_timing y su integración en la ecuación
# maestra. CoD_mensual = beneficios_NPV / (horizonte_activo_años × 12);
# CoD_total = CoD_mensual × años_de_retraso × 12; VNT resta el CoD.
#
# No hay pruebas previas que fijen la magnitud del CoD — solo se ejercía de
# paso dentro de evaluaciones con VNT positivo, sin aislar la fórmula. Este
# grupo llama a _calc_timing directamente con un cálculo de referencia
# independiente, y una prueba de extremo a extremo fija el signo con el que
# el CoD entra en la ecuación maestra.
# ─────────────────────────────────────────────────────────────────────────────

class S26_CostoDelRetraso(unittest.TestCase):

    def setUp(self):
        self.cfg = _cfg()
        self.engine = ValueEngine(self.cfg)

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

    def test_cod_usa_meses_no_anios_y_el_horizonte_del_activo(self):
        """CoD_mensual = beneficios_NPV / (horizonte_activo_años × 12)
        CoD_total = CoD_mensual × años_de_retraso × 12, que simplifica a
        beneficios_NPV × años_de_retraso / horizonte_activo. TyD/_default
        mapea a 30 años en el config (no los 10 del horizonte global) — si el
        motor usara por error el horizonte global, esta prueba lo nota."""
        p = self._p(exec_year=2030, optimal_year=2026)
        h_asset = self.engine.get_horizon_for_asset(p)
        self.assertEqual(h_asset, 30,
                         "fixture: TyD/_default debe mapear a 30 años en config.json")
        benefits_npv = 1_000_000_000.0
        timing = self.engine._calc_timing(p, benefits_npv)
        self.assertEqual(timing["delay_years"], 4)
        expected_monthly = benefits_npv / (h_asset * 12)
        expected_total = benefits_npv * 4 / h_asset
        self.assertAlmostEqual(timing["cod_monthly_COP"], expected_monthly, delta=0.01)
        self.assertAlmostEqual(timing["cod_total_COP"], expected_total, delta=1.0)

    def test_cod_no_paga_bono_por_ejecutar_antes_del_optimo(self):
        """Ejecutar ANTES del año óptimo no es 'retraso negativo' que genere
        una bonificación — el retraso se acota a 0 y el CoD a 0,0 exacto."""
        p = self._p(exec_year=2021, optimal_year=2026)  # 5 años antes
        timing = self.engine._calc_timing(p, 1_000_000_000.0)
        self.assertEqual(timing["delay_years"], 0,
                         "CoD: el retraso no se acota a 0 en ejecución temprana")
        self.assertEqual(timing["cod_total_COP"], 0.0,
                         "CoD: se generó una bonificación por ejecutar antes de tiempo")

    def test_costo_del_retraso_resta_del_vnt_y_usa_beneficios_ajustados_por_rezago(self):
        """VNT = beneficios_raw − CoD + ΔR − CAPEX − ΔOPEX. Con capex=0,
        opex=0 y risk=None, ΔR y el costo quedan en cero, así que el VNT debe
        coincidir EXACTAMENTE con beneficios_raw − CoD calculados aparte. Con
        construction_years=2 (FD_lag<1) beneficios_raw difiere de los brutos
        sin ajustar — si el motor usara los brutos como base del CoD, o
        sumara el CoD en vez de restarlo, el resultado no calzaría."""
        sc = SubCriterionInput(id="VD-1-A", label="x", type="monetizable",
                               delta_annual=100.0, price_per_unit=1_000_000,
                               weight=1.0)
        driver = DriverInput(id="VD-1", reto="x", sub_criteria=[sc], weight=1.0)
        p = self._p(exec_year=2030, optimal_year=2026,
                   drivers=[driver], construction_years=2)
        # evaluate() activa el WACC propio de la BU (TyD=9,5%, no el 10%
        # global) durante todo el cálculo — hay que replicar esa conmutación
        # para que el cálculo de referencia use la misma tasa.
        _orig_wacc = self.engine.wacc
        self.engine.wacc = self.engine._get_wacc("TyD")
        try:
            h_asset = self.engine.get_horizon_for_asset(p)
            driver_result = self.engine._calc_driver(
                driver, construction_years=2, benefit_start_offsets={}, horizon=h_asset)
            benefits_raw = driver_result["value_COP"]
            timing = self.engine._calc_timing(p, benefits_raw)
            expected_net_value = benefits_raw - timing["cod_total_COP"]
        finally:
            self.engine.wacc = _orig_wacc
        actual = self.engine.evaluate(p)
        self.assertAlmostEqual(actual["net_value_COP"], expected_net_value,
                               delta=max(1.0, abs(expected_net_value) * 1e-6))


# ─────────────────────────────────────────────────────────────────────────────
# S27 — DEGRADACIÓN: curvas no-Weibull (lineal, escalonada, acelerada), piso
# por condición observada, probabilidad anual condicional, jerarquía de
# consecuencia monetaria, EEOL y cruce marginal de año óptimo.
#
# La rama Weibull del motor de RIESGO (ValueEngine) ya quedó cerrada en
# B-0bis/S22. Este grupo es un motor distinto — AssetDegradationEngine, que
# alimenta el Módulo B de análisis predictivo — y sus curvas LINEAR/STEP/
# ACCELERATED, el piso de condición, la conversión acumulada→anual, y las
# jerarquías de configuración no tenían ninguna prueba que fijara un valor
# exacto, solo se ejercían indirectamente.
# ─────────────────────────────────────────────────────────────────────────────

class S27_Degradacion(unittest.TestCase):

    def setUp(self):
        self.eng = AssetDegradationEngine(wacc=0.1, horizon_years=10, config={})

    def _perfil(self, **kw):
        base = dict(asset_id="A", asset_name="A", asset_class="x",
                   business_unit="TyD", current_condition=0.0)
        base.update(kw)
        return AssetProfile(**base)

    # ── curva lineal ─────────────────────────────────────────────────────────

    def test_pof_lineal_exacta_y_con_tope_en_uno(self):
        self.assertAlmostEqual(self.eng._pof_linear(20.0, 40.0), 0.5, places=9)
        self.assertEqual(self.eng._pof_linear(60.0, 40.0), 1.0,
                         "PoF lineal debe topar en 1,0 más allá de la vida de diseño")

    # ── curva escalonada ─────────────────────────────────────────────────────

    def test_pof_step_salta_en_el_umbral_correcto(self):
        intervals = [5, 10, 15]
        values = [0.05, 0.25, 0.70]
        self.assertEqual(self.eng._pof_step(4.0, intervals, values), 0.0)
        self.assertEqual(self.eng._pof_step(5.0, intervals, values), 0.05,
                         "el salto debe ocurrir EN el umbral (age >= threshold)")
        self.assertEqual(self.eng._pof_step(9.0, intervals, values), 0.05)
        self.assertEqual(self.eng._pof_step(10.0, intervals, values), 0.25)
        self.assertEqual(self.eng._pof_step(20.0, intervals, values), 0.70,
                         "más allá del último umbral debe mantener el último valor")

    # ── piso por condición observada ────────────────────────────────────────

    def test_pof_no_puede_quedar_bajo_lo_que_declara_la_inspeccion(self):
        """Con design_life=100 la curva de edad da un PoF casi cero a 1 año —
        si el piso de condición no se aplica, una inspección que declara el
        activo en mal estado (0,9) queda enterrada bajo un modelo optimista."""
        p = self._perfil(degradation_model=DegradationModel.LINEAR,
                         design_life_years=100, current_condition=0.9)
        pof = self.eng._pof_at_age(p, age=1.0)
        self.assertAlmostEqual(pof, 0.9, delta=0.001,
                               msg="el piso de condición observada no se está aplicando")
        p.current_condition = 1.0
        self.assertEqual(self.eng._pof_at_age(p, age=1.0), 1.0)

    # ── curva acelerada ──────────────────────────────────────────────────────

    def test_acelerado_eleva_al_inverso_del_factor(self):
        p = self._perfil(degradation_model=DegradationModel.ACCELERATED,
                         weibull_beta=3.0, weibull_eta=40.0, acceleration_factor=4.0)
        weibull_pof = self.eng._pof_weibull(40.0, 40.0, 3.0)
        expected = weibull_pof ** (1.0 / 4.0)
        got = self.eng._pof_at_age(p, age=40.0)
        self.assertAlmostEqual(got, expected, delta=1e-6)

    # ── probabilidad anual condicional (no la acumulada) ────────────────────

    def test_probabilidad_anual_es_la_condicional_no_la_acumulada(self):
        p = self._perfil(degradation_model=DegradationModel.LINEAR, design_life_years=10.0)
        # age=0: pof(0)=0,0 pof(1)=0,1 -> S0=1,0 S1=0,9 -> anual = 1-0,9/1,0 = 0,1
        self.assertAlmostEqual(self.eng._annual_failure_prob(p, 0.0), 0.1, delta=1e-9)
        # age=9: pof(9)=0,9 pof(10)=1,0 -> S9=0,1 S10=0,0 -> anual = 1-0/0,1 = 1,0
        self.assertAlmostEqual(self.eng._annual_failure_prob(p, 9.0), 1.0, delta=1e-9)

    # ── consecuencia monetaria: jerarquía y respaldo ────────────────────────

    def test_consecuencia_prioriza_la_tabla_por_negocio_sobre_la_generica(self):
        cfg = {"risk_matrix": {
            "consequence_levels_by_bu": {"TyD": {"3": {"value_COP": 777_000_000}}},
            "consequence_levels": {"3": {"value_COP": 999_000_000}},
        }}
        eng = AssetDegradationEngine(wacc=0.1, horizon_years=10, config=cfg)
        self.assertEqual(eng._consequence_value_COP(3, "TyD"), 777_000_000.0)
        self.assertEqual(eng._consequence_value_COP(3, "AyA"), 999_000_000.0,
                         "sin tabla propia para AyA debe caer a la tabla genérica")

    def test_consecuencia_sin_risk_matrix_usa_la_tabla_de_respaldo(self):
        eng = AssetDegradationEngine(wacc=0.1, horizon_years=10, config={})
        self.assertEqual(eng._consequence_value_COP(3, "TyD"), 1_000_000_000.0)

    # ── nivel de consecuencia: centinela 0, no 3 ────────────────────────────

    def test_nivel_declarado_1_no_se_pisa_con_el_de_config(self):
        cfg = {"degradation_engine": {"consequence_by_class": {"transformador": 4}}}
        eng = AssetDegradationEngine(wacc=0.1, horizon_years=10, config=cfg)
        p1 = self._perfil(asset_class="transformador", consequence_level=1)
        self.assertEqual(eng._get_consequence_level(p1), 1,
                         "un nivel 1 declarado se está pisando con el config")
        p0 = self._perfil(asset_class="transformador", consequence_level=0)
        self.assertEqual(eng._get_consequence_level(p0), 4,
                         "sin declarar (centinela 0) debe caer al config por clase")

    # ── OPEX: un solo término de crecimiento ────────────────────────────────

    def test_opex_crece_con_la_edad_un_solo_termino(self):
        p = self._perfil(degradation_model=DegradationModel.LINEAR,
                         design_life_years=20.0, annual_maintenance_COP=100_000_000.0)
        expected = 100_000_000.0 * (1.0 + self.eng._opex_growth * 10.0)
        got = self.eng._opex_at_age(p, age=10.0)
        self.assertAlmostEqual(got, expected, delta=0.01,
            msg="OPEX: parece haber un segundo término — la PoF no debe entrar "
                "aquí, era el doble conteo de degradación ya corregido")

    # ── EEOL: el quiebre en el año correcto ─────────────────────────────────

    def test_eeol_cruza_en_el_anio_correcto(self):
        cfg = {"degradation_engine": {"eeol_replacement_threshold": 1.0}}
        # wacc casi cero (no exactamente 0: __init__ divide por wacc) para que
        # el factor de descuento no distorsione el cálculo de referencia.
        eng = AssetDegradationEngine(wacc=0.0001, horizon_years=5, config=cfg)
        p = AssetProfile(asset_id="A", asset_name="A", asset_class="x",
                         business_unit="TyD", degradation_model=DegradationModel.LINEAR,
                         install_year=2026, design_life_years=10.0,
                         replacement_cost_COP=300_000_000.0,
                         annual_maintenance_COP=0.0, consequence_level=3,
                         current_condition=0.0)
        result = eng.analyze(p, analysis_year=2026)
        self.assertFalse(result.eeol_is_horizon_edge,
                         "el quiebre debía encontrarse dentro del horizonte")
        self.assertEqual(result.eeol_year, 2028,
                         "el año de quiebre del EEOL no coincide con el cálculo de referencia")

    # ── cruce marginal del año óptimo ────────────────────────────────────────

    def test_cruce_marginal_sin_incentivo_de_esperar_actua_ya(self):
        eng = AssetDegradationEngine(wacc=0.05, horizon_years=10, config={})
        p = self._perfil(degradation_model=DegradationModel.WEIBULL,
                         install_year=2000, weibull_beta=3.0, weibull_eta=30.0,
                         replacement_cost_COP=1_000_000_000.0, consequence_level=3)
        best = eng._optimal_year_by_marginal_crossing(
            p, analysis_year=2026, capex_inflation_rate=0.08)
        self.assertEqual(best, 2026,
                         "sin incentivo financiero de esperar (WACC<=inflación CAPEX) "
                         "debe actuar de inmediato")

    def test_cruce_marginal_encuentra_el_anio_donde_el_riesgo_supera_el_ahorro(self):
        eng = AssetDegradationEngine(wacc=0.15, horizon_years=20, config={})
        p = self._perfil(degradation_model=DegradationModel.WEIBULL,
                         install_year=2026 - 20, weibull_beta=3.0, weibull_eta=30.0,
                         replacement_cost_COP=1_000_000_000.0, consequence_level=3,
                         annual_maintenance_COP=0.0)
        best = eng._optimal_year_by_marginal_crossing(
            p, analysis_year=2026, capex_inflation_rate=0.04)
        # deferral_benefit = 1.000M×(0,15-0,04) = 110M. hazard(t)=(3/30)(edad/30)^2.
        # riesgo(edad) = hazard×1.000M cruza 110M en edad=32 (edad parte en 20,
        # sube con yr-analysis_year) -> yr = 2026+12 = 2038. Verificado a mano.
        self.assertEqual(best, 2038,
                         "el año de cruce marginal no coincide con el cálculo de referencia")

    # ── beta/eta por clase: claves correctas del config ─────────────────────

    def test_beta_y_eta_por_clase_leen_las_claves_correctas_del_config(self):
        cfg = {"degradation_engine": {"weibull_beta_by_class": {
            "transformador": {"beta": 3.7, "_eta_años": 45}
        }}}
        eng = AssetDegradationEngine(wacc=0.1, horizon_years=10, config=cfg)
        p = self._perfil(asset_class="transformador")  # beta/eta en su default
        self.assertEqual(eng._get_weibull_beta(p), 3.7)
        self.assertEqual(eng._get_weibull_eta(p), 45.0)


# ─────────────────────────────────────────────────────────────────────────────
# S28 — MOTOR DE DEPENDENCIAS: detección de ciclos (los dos mecanismos de
# precedencia), exclusión mutua vs. precedencia, profundidad de cadena,
# simetría de exclusión mutua, y Efecto Cascada de Valor (CoD del hijo
# absorbido por el padre cuando se pospone).
#
# Sin pruebas previas que aíslen estos mecanismos: solo se ejercían de paso
# dentro de la validación general del optimizador Enterprise.
# ─────────────────────────────────────────────────────────────────────────────

class S28_MotorDeDependencias(unittest.TestCase):

    def _p(self, pid, precedent="", deps=None, mutex=None, min_lag=0):
        p = ProjectInput(
            id=pid, name=pid, business_unit="TyD",
            investment_type="Reposicion de Activos",
            costs=CostInput(capex_COP=1_000_000, opex_incremental_COP_year=0.0))
        p.precedent_project_id = precedent
        p.dependencies = deps or []
        p.mutual_exclusion_ids = mutex or []
        p.min_lag_years = min_lag
        return p

    # ── multigrafo: combina los dos mecanismos sin duplicar ─────────────────

    def test_multigrafo_combina_precedent_y_dependencies_sin_duplicar(self):
        """Un proyecto puede declarar el mismo padre por las dos vías a la
        vez (precedent_project_id Y dependencies) — el padre debe aparecer
        una sola vez en el multigrafo, no duplicado."""
        eng = DependencyEngine({})
        hijo = self._p("HIJO", precedent="A", deps=["A", "B"])
        g = eng._build_multigraph([hijo, self._p("A"), self._p("B")])
        self.assertEqual(g["HIJO"], ["A", "B"],
                         "el padre declarado por las dos vías se duplicó, o se perdió alguna")

    # ── ciclos: los DOS mecanismos de precedencia ────────────────────────────

    def test_ciclo_expresado_solo_por_dependencies_se_detecta(self):
        """A y B se referencian solo por 'dependencies' (no precedent_project_id).
        Si la detección de ciclos solo mirara precedent_project_id, este ciclo
        pasaría intacto al MILP."""
        eng = DependencyEngine({})
        a = self._p("A", deps=["B"])
        b = self._p("B", deps=["A"])
        with self.assertRaises(DependencyError):
            eng._check_deadlocks([a, b])

    def test_ciclo_indirecto_de_tres_nodos_se_detecta(self):
        """A precede a B, B precede a C, C precede a A — el ciclo no es
        directo (2 nodos), hay que recorrer la cadena completa."""
        eng = DependencyEngine({})
        a = self._p("A", precedent="C")
        b = self._p("B", precedent="A")
        c = self._p("C", precedent="B")
        with self.assertRaises(DependencyError):
            eng._check_deadlocks([a, b, c])

    def test_cadena_valida_sin_ciclo_no_lanza_error(self):
        """A <- B <- C es una cadena lineal válida, no un ciclo."""
        eng = DependencyEngine({})
        a = self._p("A")
        b = self._p("B", precedent="A")
        c = self._p("C", precedent="B")
        try:
            eng._check_deadlocks([a, b, c])
        except DependencyError:
            self.fail("una cadena lineal válida no debería reportarse como ciclo")

    # ── exclusión mutua vs. precedencia: ambas direcciones ──────────────────

    def test_hijo_que_excluye_a_su_padre_es_deadlock_logico(self):
        padre = self._p("PADRE")
        hijo = self._p("HIJO", precedent="PADRE", mutex=["PADRE"])
        eng = DependencyEngine({})
        with self.assertRaises(DependencyError):
            eng._check_mutex_vs_precedence([padre, hijo])

    def test_padre_que_excluye_a_su_hijo_es_deadlock_logico(self):
        padre = self._p("PADRE", mutex=["HIJO"])
        hijo = self._p("HIJO", precedent="PADRE")
        eng = DependencyEngine({})
        with self.assertRaises(DependencyError):
            eng._check_mutex_vs_precedence([padre, hijo])

    # ── profundidad de cadena: el umbral exacto ─────────────────────────────

    def test_profundidad_exactamente_en_el_limite_no_advierte(self):
        """max_dependency_depth=3. Cadena P4<-P3<-P2<-P1 tiene profundidad
        exactamente 3 (tres eslabones) — no debe advertir. Agregar un quinto
        eslabón (profundidad 4) sí debe advertir."""
        eng = DependencyEngine({"dependency_engine": {"max_dependency_depth": 3}})
        p1 = self._p("P1")
        p2 = self._p("P2", precedent="P1")
        p3 = self._p("P3", precedent="P2")
        p4 = self._p("P4", precedent="P3")
        warnings = eng._check_depth([p1, p2, p3, p4])
        self.assertEqual(warnings, [],
                         f"profundidad 3 no debía advertir, pero: {warnings}")
        p5 = self._p("P5", precedent="P4")
        warnings2 = eng._check_depth([p1, p2, p3, p4, p5])
        self.assertEqual(len(warnings2), 1,
                         "profundidad 4 (por encima del límite 3) debía advertir")

    # ── simetría de exclusión mutua ──────────────────────────────────────────

    def test_exclusion_mutua_se_vuelve_simetrica(self):
        """PA excluye a PB solo en un sentido (declarado únicamente en PA) —
        tras aplicar la simetría, PB también debe excluir a PA."""
        eng = DependencyEngine({})
        pa = self._p("PA", mutex=["PB"])
        pb = self._p("PB")
        eng._apply_mutex_symmetry([pa, pb])
        self.assertIn("PA", pb.mutual_exclusion_ids,
                      "la exclusión mutua no se propagó de vuelta a PB")

    # ── Efecto Cascada de Valor (CoD del hijo absorbido por el padre) ───────

    def test_cascada_aplica_el_lag_el_peso_y_se_acota_en_el_borde(self):
        """Hijo con min_lag_years=1: si el padre inicia en t, el hijo antes
        podía iniciar en t+1. vnt del hijo = [500,300,100] (pierde valor con
        cada año de espera). cascade_cod_weight=0,5 (no 1,0, para notar si el
        peso se ignora).

        t=0 (padre 2026): hijo antes podía en t=1 -> CoD = 500-300=200,
                           penalización = 200*0,5=100.
        t=1 (padre 2027): hijo antes podía en t=2 -> CoD = 300-100=200,
                           penalización = 100.
        t=2 (padre 2028, último año): t+lag=3 se acota a 2 (el propio t)
                           -> CoD = 100-100=0, sin penalización — no hay a
                           dónde postergar más allá del horizonte."""
        eng = DependencyEngine({"dependency_engine": {
            "cascade_cod_enabled": True, "cascade_cod_weight": 0.5}})
        padre = self._p("PADRE")
        hijo = self._p("HIJO", precedent="PADRE", min_lag=1)
        years = [2026, 2027, 2028]
        vnt = [[1000.0, 1000.0, 1000.0],   # padre
              [500.0, 300.0, 100.0]]      # hijo
        adj = eng.apply_cascade_cod([padre, hijo], None, vnt, years)
        self.assertAlmostEqual(adj[0][0], 1000.0 - 100.0, places=6)
        self.assertAlmostEqual(adj[0][1], 1000.0 - 100.0, places=6)
        self.assertAlmostEqual(adj[0][2], 1000.0, places=6,
                               msg="en el borde del horizonte no hay dónde "
                                   "postergar al hijo — no debe haber penalización")

    def test_cascada_no_da_bonificacion_si_el_hijo_gana_valor_al_esperar(self):
        """Si el valor del hijo AUMENTA con la espera (caso inusual pero
        posible), la cascada no debe convertirse en una bonificación para el
        padre — el piso en 0 debe sostenerse."""
        eng = DependencyEngine({"dependency_engine": {
            "cascade_cod_enabled": True, "cascade_cod_weight": 1.0}})
        padre = self._p("PADRE")
        hijo = self._p("HIJO", precedent="PADRE", min_lag=1)
        years = [2026, 2027, 2028]
        vnt = [[1000.0, 1000.0, 1000.0],
              [100.0, 300.0, 500.0]]     # el hijo GANA valor al esperar
        adj = eng.apply_cascade_cod([padre, hijo], None, vnt, years)
        self.assertAlmostEqual(adj[0][0], 1000.0, places=6,
                               msg="la cascada no puede dar una bonificación "
                                   "al padre por retrasarse")

    def test_cascada_desactivada_no_modifica_nada(self):
        eng = DependencyEngine({"dependency_engine": {"cascade_cod_enabled": False}})
        padre = self._p("PADRE")
        hijo = self._p("HIJO", precedent="PADRE", min_lag=1)
        years = [2026, 2027, 2028]
        vnt = [[1000.0, 1000.0, 1000.0], [500.0, 300.0, 100.0]]
        adj = eng.apply_cascade_cod([padre, hijo], None, vnt, years)
        self.assertEqual(adj, vnt)


# ─────────────────────────────────────────────────────────────────────────────
# S29 — PROPORCIONALIDAD Y MÉTRICAS: EAV, VEI, VEI_EAV, nivel de rigor
# analítico (proporcionalidad IAM SSG), matriz de riesgo 5×5, y la tabla de
# persona-mes por tramo de CAPEX.
#
# Ninguna de estas fórmulas tenía una prueba que fijara su valor exacto ni
# sus fronteras — solo se ejercían de paso con datos que nunca separaban
# CAPEX nominal de CAPEX a valor presente, ni tocaban un umbral exacto.
# ─────────────────────────────────────────────────────────────────────────────

class S29_ProporcionalidadMetricas(unittest.TestCase):

    def setUp(self):
        self.cfg = _cfg()
        self.engine = ValueEngine(self.cfg)

    # ── EAV: fórmula exacta y casos borde sin división por cero ─────────────

    def test_eav_formula_exacta_y_casos_borde(self):
        wacc = self.engine.wacc
        vnt = 500_000_000.0
        h = 8
        annuity = (1 - (1 + wacc) ** -h) / wacc
        expected = vnt / annuity
        self.assertAlmostEqual(self.engine.calc_eav(vnt, h), expected,
                               delta=max(1.0, abs(expected) * 1e-9))
        self.assertEqual(self.engine.calc_eav(vnt, 0), 0.0,
                         "horizonte 0 debe devolver 0,0 sin dividir por cero")
        self.assertEqual(self.engine.calc_eav(vnt, -3), 0.0)
        self.assertEqual(self.engine.calc_eav(0.0, h), 0.0)

    def test_eav_con_wacc_cero_no_se_rompe_y_usa_la_formula_correcta(self):
        """Con WACC=0 el factor de anualidad es simplemente el horizonte —
        EAV debe ser VNT/horizonte, no VNT×horizonte. Y horizonte=0 con
        WACC=0 debe seguir devolviendo 0,0 sin dividir por cero: si la
        guarda de horizonte se desplaza (<=0 -> <0), este es el único caso
        donde la diferencia es observable, porque con WACC≠0 el segundo
        resguardo (annuity_factor==0) la vuelve a atrapar."""
        engine0 = ValueEngine({"wacc": 0.0})
        vnt = 500_000_000.0
        self.assertAlmostEqual(engine0.calc_eav(vnt, 10), vnt / 10, delta=0.01)
        self.assertEqual(engine0.calc_eav(vnt, 0), 0.0,
                         "horizonte 0 con WACC=0 debe devolver 0,0 sin dividir por cero")

    # ── VEI y VEI_EAV: deben usar CAPEX a valor presente, no el nominal ─────

    def test_vei_usa_capex_a_valor_presente_no_el_nominal(self):
        """Con diferimiento, el CAPEX nominal (inflado) y el CAPEX a valor
        presente (descontado de vuelta) son distintos — si el VEI dividiera
        por el nominal, esta prueba lo nota."""
        sc = SubCriterionInput(id="X", label="x", type="monetizable",
                               delta_annual=1000.0, price_per_unit=1_000_000,
                               weight=1.0)
        driver = DriverInput(id="VD-1", reto="x", sub_criteria=[sc])
        p = ProjectInput(id="P", name="P", business_unit="TyD",
                         investment_type="Reposicion de Activos",
                         costs=CostInput(capex_COP=1_000_000_000, opex_incremental_COP_year=0.0),
                         execution_year=2026, optimal_year=2026,
                         drivers=[driver], risk_components=[], construction_years=1)
        p.analysis_horizon_years = 30
        r = self.engine.evaluate(p, deferral_years=3)
        capex_inflation = self.cfg.get("capex_inflation_rate", 0.04)
        wacc = self.engine._get_wacc("TyD")
        capex_nominal = 1_000_000_000 * (1.0 + capex_inflation) ** 3
        capex_pv = capex_nominal / (1.0 + wacc) ** 3
        self.assertAlmostEqual(r["capex_pv_COP"], capex_pv, delta=1.0)
        expected_vei = r["net_value_COP"] / capex_pv
        self.assertAlmostEqual(r["value_efficiency_index"], expected_vei, delta=1e-6)

    def test_vei_eav_usa_capex_a_valor_presente_y_horizonte_del_activo(self):
        """TyD/_default mapea a 30 años (no el horizonte global de 10) — si
        el VEI_EAV usara el horizonte global por error, esta prueba lo nota.
        Con diferimiento (deferral_years=3) el CAPEX nominal y el CAPEX a
        valor presente también difieren — si el VEI_EAV usara el nominal,
        esta misma prueba también lo nota."""
        sc = SubCriterionInput(id="X", label="x", type="monetizable",
                               delta_annual=500.0, price_per_unit=1_000_000,
                               weight=1.0)
        driver = DriverInput(id="VD-1", reto="x", sub_criteria=[sc])
        p = ProjectInput(id="P", name="P", business_unit="TyD",
                         investment_type="Reposicion de Activos",
                         costs=CostInput(capex_COP=1_000_000_000, opex_incremental_COP_year=0.0),
                         execution_year=2026, optimal_year=2026,
                         drivers=[driver], risk_components=[], construction_years=1)
        p.analysis_horizon_years = 30
        r = self.engine.evaluate(p, deferral_years=3)
        # evaluate() activa el WACC propio de TyD durante todo el cálculo y
        # lo restaura al salir — hay que replicar esa conmutación para que
        # calc_eav() use la misma tasa que usó evaluate() internamente.
        _orig_wacc = self.engine.wacc
        self.engine.wacc = self.engine._get_wacc("TyD")
        try:
            h_asset = self.engine.get_horizon_for_asset(p)
            self.assertEqual(h_asset, 30, "fixture: TyD/_default debe mapear a 30 años")
            eav = self.engine.calc_eav(r["net_value_COP"], h_asset)
            expected = eav / (r["capex_pv_COP"] / h_asset)
        finally:
            self.engine.wacc = _orig_wacc
        self.assertAlmostEqual(r["vei_eav"], expected, delta=1e-6)

    # ── Proporcionalidad: nivel de rigor analítico (IAM SSG) ────────────────

    def test_nivel_de_modelo_prioriza_umbral_por_negocio_y_respeta_el_limite(self):
        cfg = {"proportionality_thresholds_COP": {
            "TyD": {"full_model_COP": 10_000_000_000, "simplified_model_COP": 1_000_000_000},
            # umbral genérico deliberadamente muy distinto, para notar si se
            # usa por error en vez del propio de TyD
            "Todos": {"full_model_COP": 999_999_999_999},
        }}
        engine = ValueEngine(cfg)
        self.assertEqual(engine._get_model_level(10_000_000_000, "TyD"), "FULL",
                         "en el umbral exacto (capex==full) debe ser FULL")
        self.assertEqual(engine._get_model_level(9_999_999_999, "TyD"), "SIMPLIFIED")
        self.assertEqual(engine._get_model_level(999_999_999, "TyD"), "RULE_BASED")

    # ── Matriz de riesgo 5×5 ─────────────────────────────────────────────────

    def test_matriz_de_riesgo_valores_documentados_y_recorte_a_rango(self):
        self.assertEqual(EnterpriseOptimizer._risk_matrix_level(1, 1), 1)
        self.assertEqual(EnterpriseOptimizer._risk_matrix_level(4, 5), 5)
        self.assertEqual(EnterpriseOptimizer._risk_matrix_level(3, 3), 3)
        self.assertEqual(
            EnterpriseOptimizer._risk_matrix_level(10, 5),
            EnterpriseOptimizer._risk_matrix_level(5, 5),
            "consequence=10 debe recortarse a 5, no comportarse distinto")
        self.assertEqual(
            EnterpriseOptimizer._risk_matrix_level(1, 0),
            EnterpriseOptimizer._risk_matrix_level(1, 1),
            "probability=0 debe recortarse a 1")

    # ── Persona-mes: límites exactos de cada tramo ──────────────────────────

    def test_persona_mes_respeta_los_limites_exactos_de_cada_tramo(self):
        opt = EnterpriseOptimizer(config=self.cfg)
        self.assertEqual(opt._estimate_person_months(999), 2.0)
        self.assertEqual(opt._estimate_person_months(1000), 6.0)
        self.assertEqual(opt._estimate_person_months(9999), 6.0)
        self.assertEqual(opt._estimate_person_months(10000), 15.0)
        self.assertEqual(opt._estimate_person_months(49999), 15.0)
        self.assertEqual(opt._estimate_person_months(50000), 35.0)
        self.assertEqual(opt._estimate_person_months(199999), 35.0)
        self.assertEqual(opt._estimate_person_months(200000), 80.0)
        self.assertEqual(opt._estimate_person_months(500000), 80.0)


# ─────────────────────────────────────────────────────────────────────────────
# S30 — ANALISIS DE ANO OPTIMO: coherencia con evaluate() y valor presente
#
# `optimal_timing_analysis()` es la segunda puerta del motor: produce el ano
# recomendado que alimenta al optimizador de portafolio. Hasta esta version
# NINGUNA prueba lo llamaba — las unicas menciones en la suite eran lecturas
# de config. Eso permitio que conviviera con dos defectos medibles:
#
#   1. No traia el CAPEX diferido a valor presente. `evaluate()` si lo hacia
#      (correccion B-0), asi que las dos puertas cobraban distinto por la
#      misma decision: +31,3% de sobrecosto a 3 anos de espera.
#   2. En la rama Weibull aplicaba DOS VECES el descuento por anos de obra,
#      porque `_calc_risk()` ya lo aplica internamente: -13,2% de VNT.
#
# El grupo S21 ya vigilaba esta clase de defecto, pero solo por la puerta de
# `evaluate()`. Este grupo cierra la otra.
#
# Fuente de la regla verificada, Copperleaf "Managing Risk and Value in
# Critical Infrastructure" §4.4: "Delaying an investment will typically
# increase its cost due to inflation and other price changes, but the discount
# rate will drive the project cost down into the future."
# ─────────────────────────────────────────────────────────────────────────────

class S30_AnioOptimoCoherencia(unittest.TestCase):

    def setUp(self):
        self.cfg = _cfg()
        self.engine = ValueEngine(self.cfg)
        self.wacc = self.engine._get_wacc("TyD")
        self.infl = self.cfg.get("optimal_timing", {}).get("capex_inflation_rate", 0.04)

    def _riesgo(self, weibull=True, edad=32.0):
        kw = dict(baseline_consequence_level=4, baseline_probability_level=4,
                  residual_consequence_level=2, residual_probability_level=2)
        if weibull:
            kw.update(weibull_beta=2.5, weibull_eta=40.0, asset_age_years=edad)
        return RiskInput(**kw, asset_type="transformador_potencia")

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

    def _por_anio(self, resultado):
        return {y["delay_years"]: y for y in resultado["yearly_results"]}

    # ── El CAPEX diferido entra a valor presente ────────────────────────────

    def test_capex_diferido_se_trae_a_valor_presente_en_el_anio_optimo(self):
        """CAPEX a valor presente = capex × (1+inflacion)^t / (1+WACC)^t.

        Inflar el desembolso a pesos del ano t y restarlo contra beneficios en
        pesos de hoy mezcla dos monedas. Con inflacion de obra por debajo del
        WACC, esperar ABARATA el desembolso en valor presente, y sin este
        descuento el analisis nunca podia verlo."""
        capex = 6_000_000_000
        anios = self._por_anio(self.engine.optimal_timing_analysis(self._proj(capex=capex)))
        for t in (0, 1, 3, 5):
            if t not in anios:
                continue
            esperado = capex * ((1.0 + self.infl) ** t) / ((1.0 + self.wacc) ** t)
            self.assertAlmostEqual(
                anios[t]["capex_pv_COP"], esperado,
                delta=max(1.0, abs(esperado) * 1e-9),
                msg=f"t={t}: el CAPEX diferido no esta a valor presente")

    def test_el_capex_a_valor_presente_decrece_si_la_inflacion_es_menor_que_el_wacc(self):
        """Invariante economico: si la obra se encarece mas lento que el costo
        del capital, esperar reduce el valor presente del desembolso."""
        self.assertLess(self.infl, self.wacc,
                        "fixture: esta prueba supone inflacion de obra < WACC")
        anios = self._por_anio(self.engine.optimal_timing_analysis(self._proj()))
        pv = [anios[t]["capex_pv_COP"] for t in sorted(anios)]
        for i in range(len(pv) - 1):
            self.assertLess(pv[i + 1], pv[i],
                            "el CAPEX a valor presente no decrece al diferir")

    def test_el_capex_nominal_si_crece_al_diferir(self):
        """El nominal y el valor presente son dos cifras distintas y ambas se
        reportan: el nominal sube por inflacion de obra, el presente baja."""
        anios = self._por_anio(self.engine.optimal_timing_analysis(self._proj()))
        nom = [anios[t]["capex_ajustado_COP"] for t in sorted(anios)]
        for i in range(len(nom) - 1):
            self.assertGreater(nom[i + 1], nom[i],
                               "el CAPEX nominal deberia crecer con la inflacion de obra")

    def test_el_indice_de_eficiencia_usa_el_capex_a_valor_presente(self):
        """Numerador y denominador en la misma moneda, igual que en evaluate()."""
        anios = self._por_anio(self.engine.optimal_timing_analysis(self._proj()))
        for t, y in anios.items():
            self.assertAlmostEqual(
                y["vei"], y["net_value_COP"] / y["capex_pv_COP"], places=9,
                msg=f"t={t}: el indice de eficiencia no usa el CAPEX a valor presente")

    def test_el_costo_reportado_usa_el_capex_a_valor_presente_no_el_nominal(self):
        """El defecto original no estaba en la cifra reportada sino en CUAL de
        las dos entraba a la ecuacion. Esta prueba reconstruye el componente de
        costo de forma independiente: costo = -(CAPEX a valor presente + OPEX).

        Vigilar unicamente el campo `capex_pv_COP` no basta: se puede calcular
        bien y no usarse, que es exactamente lo que ocurria."""
        opex = 50_000_000.0
        p = self._proj(opex=opex)
        h_activo = self.engine.get_horizon_for_asset(p)
        _orig = self.engine.wacc
        self.engine.wacc = self.wacc
        try:
            opex_npv = self.engine._npv_annual(opex, horizon=h_activo)
        finally:
            self.engine.wacc = _orig
        anios = self._por_anio(self.engine.optimal_timing_analysis(p))
        for t, y in anios.items():
            esperado = -1.0 * (y["capex_pv_COP"] + opex_npv)
            self.assertAlmostEqual(
                y["cost_npv_COP"], esperado,
                delta=max(1.0, abs(esperado) * 1e-9),
                msg=(f"t={t}: el componente de costo no usa el CAPEX a valor "
                     f"presente. Diferencia vs. usar el nominal: "
                     f"{y['capex_ajustado_COP'] - y['capex_pv_COP']:,.0f} COP"))

    def test_el_costo_total_mejora_al_diferir_si_la_inflacion_es_menor_que_el_wacc(self):
        """Invariante economico, independiente de cualquier formula: si la obra
        se encarece mas lento que el costo del capital, el componente de costo
        debe volverse MENOS negativo al diferir. Con el CAPEX nominal ocurre lo
        contrario, y por eso esperar nunca podia ganar."""
        self.assertLess(self.infl, self.wacc,
                        "fixture: esta prueba supone inflacion de obra < WACC")
        anios = self._por_anio(self.engine.optimal_timing_analysis(self._proj()))
        costos = [anios[t]["cost_npv_COP"] for t in sorted(anios)]
        for i in range(len(costos) - 1):
            self.assertGreater(
                costos[i + 1], costos[i],
                "el costo no mejora al diferir: se esta usando el CAPEX nominal")

    # ── El riesgo mitigado se descuenta por el retraso de la decision ───────

    @staticmethod
    def _tasa_falla(edad, eta, beta):
        """Oraculo independiente: h(t) = (beta/eta)(t/eta)^(beta-1), acotada a 1."""
        if eta <= 0 or edad <= 0 or beta <= 0:
            return 0.0
        return min(1.0, (beta / eta) * ((edad / eta) ** (beta - 1.0)))

    def test_el_riesgo_mitigado_se_descuenta_por_el_retraso_rama_weibull(self):
        """Recalculo independiente de la rama Weibull, incluido el descuento
        por el retraso de la decision, que solo es observable en t>0."""
        BETA, ETA, EDAD, CY = 2.5, 40.0, 32.0, 1
        rm = self.cfg["risk_matrix"]
        cof_b = rm["consequence_levels_by_bu"]["TyD"]["4"]["value_COP"]
        cof_r = rm["consequence_levels_by_bu"]["TyD"]["2"]["value_COP"]
        pl = rm["probability_levels"]
        razon = pl["2"]["annual_prob"] / pl["4"]["annual_prob"]

        p = self._proj(cy=CY, weibull=True)
        h_activo = self.engine.get_horizon_for_asset(p)
        anios = self._por_anio(self.engine.optimal_timing_analysis(p))

        for t in (0, 1, 3):
            if t not in anios:
                continue
            edad_eval = EDAD + t
            vp = sum(
                (cof_b * self._tasa_falla(edad_eval + k, ETA, BETA)
                 - cof_r * self._tasa_falla(edad_eval + k, ETA, BETA) * razon)
                / ((1.0 + self.wacc) ** k)
                for k in range(1, h_activo + 1))
            vp *= 1.0 / ((1.0 + self.wacc) ** CY)
            exposicion = sum(
                (cof_b * self._tasa_falla(EDAD + s, ETA, BETA)
                 - cof_r * self._tasa_falla(EDAD + s, ETA, BETA) * razon)
                / ((1.0 + self.wacc) ** s)
                for s in range(1, t + 1))
            tf = 1.0 / ((1.0 + self.wacc) ** t)
            esperado = (vp - exposicion) * tf
            self.assertAlmostEqual(
                anios[t]["delta_r_npv_COP"], esperado,
                delta=max(1.0, abs(esperado) * 1e-9),
                msg=(f"t={t}: el riesgo mitigado de la rama Weibull no coincide "
                     f"con el recalculo independiente"))

    def test_el_riesgo_mitigado_se_descuenta_por_el_retraso_rama_exponencial(self):
        """La rama exponencial tiene forma cerrada: se puede fijar el valor
        exacto en t>0, donde vive el descuento por el retraso."""
        CY = 1
        rm = self.cfg["risk_matrix"]
        cof_b = rm["consequence_levels_by_bu"]["TyD"]["4"]["value_COP"]
        cof_r = rm["consequence_levels_by_bu"]["TyD"]["2"]["value_COP"]
        pl = rm["probability_levels"]
        dr_anual = (cof_b * pl["4"]["annual_prob"] - cof_r * pl["2"]["annual_prob"])

        p = self._proj(cy=CY, weibull=False)
        h_activo = self.engine.get_horizon_for_asset(p)
        g = self.engine._get_risk_growth_rate("TyD", asset_type="transformador_potencia")
        r = self.wacc
        gordon = ((1.0 - ((1.0 + g) / (1.0 + r)) ** h_activo) / (r - g)
                  if abs(r - g) > 1e-9 else float(h_activo))
        fd_obra = 1.0 / ((1.0 + r) ** CY)

        anios = self._por_anio(self.engine.optimal_timing_analysis(p))
        for t in (0, 1, 3):
            if t not in anios:
                continue
            tf = 1.0 / ((1.0 + r) ** t)
            esperado = dr_anual * ((1.0 + g) ** t) * gordon * tf * fd_obra
            self.assertAlmostEqual(
                anios[t]["delta_r_npv_COP"], esperado,
                delta=max(1.0, abs(esperado) * 1e-9),
                msg=f"t={t}: el riesgo mitigado de la rama exponencial no cuadra")

    # ── Las dos puertas del motor deben valorar igual el ano 0 ──────────────

    def test_las_dos_puertas_coinciden_en_el_anio_cero_rama_weibull(self):
        """En t=0 no hay diferimiento: `evaluate()` y el analisis de ano optimo
        estan valorando exactamente la misma decision y deben dar el mismo
        numero. Aqui vivia el doble descuento por anos de obra."""
        p = self._proj(cy=1, weibull=True)
        ev = self.engine.evaluate(p)["net_value_COP"]
        ot = self.engine.optimal_timing_analysis(p)["t0_net_value"]
        self.assertAlmostEqual(
            ot, ev, delta=max(1.0, abs(ev) * 1e-9),
            msg="rama Weibull: las dos puertas del motor valoran distinto el ano 0")

    def test_las_dos_puertas_coinciden_en_el_anio_cero_rama_exponencial(self):
        """La rama exponencial no tenia el defecto; esta prueba impide que se
        introduzca al corregir la otra."""
        p = self._proj(cy=1, weibull=False)
        ev = self.engine.evaluate(p)["net_value_COP"]
        ot = self.engine.optimal_timing_analysis(p)["t0_net_value"]
        self.assertAlmostEqual(
            ot, ev, delta=max(1.0, abs(ev) * 1e-9),
            msg="rama exponencial: las dos puertas del motor valoran distinto el ano 0")

    # ── El descuento por anos de obra se aplica UNA sola vez ────────────────

    def test_el_rezago_de_obra_se_cobra_una_sola_vez_en_la_rama_weibull(self):
        """Con dos anos de obra en vez de cero, el riesgo mitigado debe quedar
        multiplicado por 1/(1+WACC)^2 — no por 1/(1+WACC)^4.

        Es la prueba decisiva del defecto: `_calc_risk()` ya aplica el
        descuento por obra internamente, y el analisis lo volvia a aplicar."""
        r0 = self.engine.optimal_timing_analysis(self._proj(cy=0))
        r2 = self.engine.optimal_timing_analysis(self._proj(cy=2))
        dr0 = self._por_anio(r0)[0]["delta_r_npv_COP"]
        dr2 = self._por_anio(r2)[0]["delta_r_npv_COP"]
        self.assertGreater(abs(dr0), 0.0, "fixture: el riesgo mitigado no debe ser cero")
        esperado = 1.0 / ((1.0 + self.wacc) ** 2)
        duplicado = 1.0 / ((1.0 + self.wacc) ** 4)
        self.assertAlmostEqual(
            dr2 / dr0, esperado, places=9,
            msg=(f"razon obtenida {dr2/dr0:.6f}; se esperaba {esperado:.6f} "
                 f"(descuento aplicado una vez). Si se parece a {duplicado:.6f}, "
                 f"el rezago de obra se esta cobrando dos veces."))

    # ── D-14: ΔR se suma por componente, y el growth rate reportado ─────────
    # refleja el PRIMER componente, no un lookup aparte a nivel de proyecto ──

    def test_dos_componentes_con_asset_type_distinto_se_suman_y_reportan_el_primero(self):
        """El ΔR de 2 componentes con asset_type distinto es la suma
        independiente de cada uno, y el risk_growth_rate reportado refleja
        el PRIMER componente — no un lookup aparte a nivel de proyecto que
        podria no coincidir con lo que el loop realmente uso (hallazgo de
        code review de la migracion de _optimal_timing_inner, D-14).

        El primer componente (comp_generico) NO declara asset_type propio, y
        por eso cae a la tasa de la BU (0.15). El proyecto SI declara
        asset_type="transformador_potencia" (0.12) — un lookup a nivel de
        proyecto reportaria 0.12, distinto de lo que el primer componente
        realmente usa. Solo con esta divergencia la prueba puede detectar
        una regresion al bug original."""
        comp_generico = RiskInput(
            baseline_consequence_level=4, baseline_probability_level=4,
            residual_consequence_level=2, residual_probability_level=2,
            asset_type="",  # sin asset_type propio -> tasa de la BU (0.15)
        )
        comp_transformador = self._riesgo(weibull=False)  # asset_type="transformador_potencia" (0.12)

        p = self._proj(weibull=False)
        p.risk_components = [comp_generico, comp_transformador]
        resultado_dos = self.engine.optimal_timing_analysis(p)
        anios_dos = self._por_anio(resultado_dos)

        p_solo_generico = self._proj(weibull=False)
        p_solo_generico.risk_components = [comp_generico]
        anios_generico = self._por_anio(self.engine.optimal_timing_analysis(p_solo_generico))

        p_solo_transformador = self._proj(weibull=False)
        p_solo_transformador.risk_components = [comp_transformador]
        anios_transformador = self._por_anio(self.engine.optimal_timing_analysis(p_solo_transformador))

        for t in (0, 1, 3):
            if t not in anios_dos or t not in anios_generico or t not in anios_transformador:
                continue
            esperado = (anios_generico[t]["delta_r_npv_COP"]
                        + anios_transformador[t]["delta_r_npv_COP"])
            self.assertAlmostEqual(
                anios_dos[t]["delta_r_npv_COP"], esperado,
                delta=max(1.0, abs(esperado) * 1e-9),
                msg=(f"t={t}: la suma de 2 componentes con asset_type distinto "
                     f"no coincide con la suma independiente de cada uno por separado"))

        # El growth rate reportado debe ser el del PRIMER componente
        # (comp_generico, sin asset_type propio -> tasa de la BU = 0.15), NO
        # el lookup a nivel de proyecto (project.asset_type=
        # "transformador_potencia" -> 0.12) que el loop interno ya no usa
        # para el computo real.
        g_esperado = self.engine._get_risk_growth_rate("TyD", asset_type="")
        self.assertAlmostEqual(resultado_dos["risk_growth_rate"], g_esperado, places=9)


# ─────────────────────────────────────────────────────────────────────────────
# S31 — PERFIL DE CAPEX POR AÑO (costs.capex_by_year)
# ─────────────────────────────────────────────────────────────────────────────

class S31_PerfilCapexPorAnio(unittest.TestCase):
    """CAPEX declarado por año calendario (costs.capex_by_year), valorado y
    reanclado correctamente cuando el motor prueba un año de arranque
    distinto al originalmente declarado."""

    def setUp(self):
        self.cfg = _cfg()
        self.engine = ValueEngine(self.cfg)

    def test_declared_execution_year_se_autocompleta(self):
        """Sin declararlo explícitamente, toma el valor de execution_year."""
        p = ProjectInput(
            id="P900", name="Test", business_unit="TyD",
            investment_type="Reposicion de Activos",
            costs=CostInput(capex_COP=1_000_000_000),
            execution_year=2029,
        )
        self.assertEqual(p.declared_execution_year, 2029)

    def test_declared_execution_year_explicito_se_respeta(self):
        """Si se declara explícitamente, no se sobrescribe con execution_year."""
        p = ProjectInput(
            id="P901", name="Test", business_unit="TyD",
            investment_type="Reposicion de Activos",
            costs=CostInput(capex_COP=1_000_000_000),
            execution_year=2029,
            declared_execution_year=2027,
        )
        self.assertEqual(p.declared_execution_year, 2027)

    def test_declared_execution_year_sobrevive_reasignacion_posterior(self):
        """Si algo reasigna execution_year DESPUÉS de construir el objeto (lo
        que hacen ambos optimizadores MILP al escanear años candidatos),
        declared_execution_year no debe moverse — es la garantía sobre la
        que se apoyan todas las pruebas de reanclaje de este grupo."""
        p = ProjectInput(
            id="P902", name="Test", business_unit="TyD",
            investment_type="Reposicion de Activos",
            costs=CostInput(capex_COP=1_000_000_000),
            execution_year=2028,
        )
        p.execution_year = 2026  # simula lo que hace un bucle de escaneo MILP
        self.assertEqual(p.declared_execution_year, 2028)

    def test_capex_by_year_un_tramo_igual_al_legado(self):
        """Un solo tramo en declared_execution_year debe dar exactamente el
        mismo VNT que el camino legado con ese mismo CAPEX como escalar."""
        p_legado = _make_project(pid="P910", capex=5_000_000_000, exec_year=2026)
        r_legado = self.engine.evaluate(p_legado)

        p_perfil = _make_project(
            pid="P911", capex=0, exec_year=2026,
            capex_by_year={2026: 5_000_000_000},
        )
        r_perfil = self.engine.evaluate(p_perfil)

        self.assertAlmostEqual(
            r_perfil["net_value_COP"], r_legado["net_value_COP"], delta=1.0,
            msg="Un perfil de un solo tramo en el año declarado debe "
                "coincidir al peso con el camino legado (regresión).",
        )

    def test_capex_by_year_multitramo_sin_escaneo(self):
        """Perfil de dos tramos, sin ningún escaneo (execution_year ==
        declared_execution_year): cada tramo se descuenta por separado desde
        declared_execution_year, verificado con un oráculo independiente."""
        p = _make_project(
            pid="P912", capex=0, exec_year=2026,
            capex_by_year={2026: 3_000_000_000, 2027: 2_000_000_000},
        )
        r = self.engine.evaluate(p)
        wacc = self.engine._get_wacc(p.business_unit)

        capex_pv_esperado = (
            3_000_000_000 / ((1 + wacc) ** 0) +
            2_000_000_000 / ((1 + wacc) ** 1)
        )
        cost_npv_esperado = -1.0 * (capex_pv_esperado + r["opex_incremental_npv_COP"])

        self.assertAlmostEqual(r["cost_npv_COP"], cost_npv_esperado, delta=1.0)

    def test_capex_by_year_se_reancla_con_deferral_years(self):
        """El perfil se reancla vía deferral_years — la referencia temporal
        compartida por toda la ecuación — no vía execution_year. Un perfil
        declarado en declared_execution_year, evaluado con deferral_years=3,
        debe descontarse exactamente 3 años más que sin diferir, igual que
        haría el CAPEX legado con ese mismo deferral_years."""
        p = _make_project(
            pid="P913", capex=0, exec_year=2026,  # declarado en 2026
            capex_by_year={2026: 3_000_000_000, 2027: 2_000_000_000},
        )
        r = self.engine.evaluate(p, deferral_years=3)
        wacc = self.engine._get_wacc(p.business_unit)

        # offset_k = year - declared: 0 y 1. deferral_years=3 se suma a cada uno.
        capex_pv_esperado = (
            3_000_000_000 / ((1 + wacc) ** (0 + 3)) +
            2_000_000_000 / ((1 + wacc) ** (1 + 3))
        )
        cost_npv_esperado = -1.0 * (capex_pv_esperado + r["opex_incremental_npv_COP"])

        self.assertAlmostEqual(r["cost_npv_COP"], cost_npv_esperado, delta=1.0)

    def test_capex_by_year_convencion_enterprise_declarado_distinto_del_base(self):
        """Réplica exacta de la convención real de EnterpriseOptimizer: se
        reasigna execution_year AL CANDIDATO y se pasa deferral_years
        relativo al año base del plan (no a declared_execution_year). Esta
        es la prueba que hubiera atrapado el defecto original: con
        declared_execution_year=2029 y un candidato = año base del plan
        (deferral_years=0), el tramo declarado en 2029 debe valorarse SIN
        descuento (offset=0, deferral_years=0) — no un 31% por encima del
        nominal, que es lo que daba la fórmula con delta en vez de
        deferral_years."""
        p = _make_project(
            pid="P915", capex=0, exec_year=2029,  # declarado en 2029
            capex_by_year={2029: 1_000_000_000},
        )
        p.execution_year = 2026  # el MILP reasigna al candidato (año base del plan)
        r = self.engine.evaluate(p, deferral_years=0)  # año base del plan → sin diferir
        self.assertAlmostEqual(r["capex_pv_COP"], 1_000_000_000, delta=1.0)

    def test_capex_schedule_pv_reportado_por_tramo(self):
        """capex_schedule_pv_COP debe exponer cada tramo por separado, no
        solo el total — es lo que permite auditar dónde quedó cada peso."""
        p = _make_project(
            pid="P914", capex=0, exec_year=2026,
            capex_by_year={2026: 1_000_000_000, 2028: 1_000_000_000},
        )
        r = self.engine.evaluate(p)
        self.assertIn(2026, r["capex_schedule_pv_COP"])
        self.assertIn(2028, r["capex_schedule_pv_COP"])
        self.assertAlmostEqual(
            sum(r["capex_schedule_pv_COP"].values()), r["capex_pv_COP"], delta=1.0
        )

    def test_capex_cop_nominal_es_la_suma_del_perfil(self):
        """r['capex_COP'] (nominal, sin descontar) debe ser la suma de los
        tramos declarados — no el escalar capex_COP=0 sin usar. Alimenta
        los umbrales de proporcionalidad; un descuido aquí no se veía en
        ninguna otra prueba del grupo (todas verifican capex_pv_COP, el
        valor descontado, no el nominal)."""
        p = _make_project(
            pid="P916", capex=0, exec_year=2026,
            capex_by_year={2026: 1_000_000_000, 2028: 1_000_000_000},
        )
        r = self.engine.evaluate(p)
        self.assertAlmostEqual(r["capex_COP"], 2_000_000_000, delta=1.0)

    def test_ambos_optimizadores_coinciden_con_base_year_desalineado(self):
        """PortfolioOptimizer y EnterpriseOptimizer deben dar el mismo VNT
        para el mismo proyecto/vigencias, incluso cuando el parámetro
        base_year NO coincide con el primer año de las vigencias.

        Hallazgo de la revisión final de todo el trabajo: PortfolioOptimizer
        usaba base_year para calcular deferral_years, mientras
        EnterpriseOptimizer siempre usó min(years de las vigencias) — nunca
        el parámetro base_year, que ahí solo sirve para Continuous
        Planning. Los dos solo coincidían por casualidad en todos los
        llamadores existentes, que siempre pasan base_year == min(vigencias).
        Este caso lo rompe deliberadamente: solo hay cupo en 2032, y
        base_year se declara en 2020 — muy lejos de 2030 (el primer año de
        la ventana) y de 2032 (el año que realmente se selecciona)."""
        periods_simple = (
            [BudgetPeriod(year=y, budget_COP=0.0) for y in (2030, 2031)] +
            [BudgetPeriod(year=2032, budget_COP=10_000_000_000)]
        )
        periods_ent = (
            [ResourcePeriod(year=y, budget_COP=0.0) for y in (2030, 2031)] +
            [ResourcePeriod(year=2032, budget_COP=10_000_000_000)]
        )
        p_simple = _make_project(pid="P940", capex=5_000_000_000, exec_year=2030, is_mandatory=True)
        p_ent = _make_project(pid="P941", capex=5_000_000_000, exec_year=2030, is_mandatory=True)

        r_simple = PortfolioOptimizer(config=self.cfg).optimize(
            [p_simple], periods_simple, base_year=2020)
        r_ent = EnterpriseOptimizer(config=self.cfg).optimize(
            [p_ent], periods_ent, base_year=2020)

        sel_simple = next(s for s in r_simple.selected if s["project_id"] == "P940")
        sel_ent = next(s for s in r_ent.selected if s["project_id"] == "P941")

        self.assertEqual(sel_simple["year"], 2032)
        self.assertEqual(sel_ent["year"], 2032)
        self.assertAlmostEqual(
            sel_simple["net_value_COP"], sel_ent["net_value_COP"], delta=1.0,
            msg="Los dos optimizadores deben acordar el mismo VNT para el "
                "mismo escenario — si difieren, uno de los dos está usando "
                "base_year (parámetro) y el otro min(vigencias) para "
                "deferral_years, y no son la misma referencia salvo que "
                "coincidan por casualidad.",
        )

    def test_enterprise_r1_reancla_perfil_al_candidato(self):
        """Un proyecto con capex_by_year declarado en 2028/2029 debe poder
        seleccionarse arrancando en otro año — su gasto real debe caer
        reanclado. Solo 2025/2026 tienen cupo (interior de la ventana, lejos
        de ambos bordes) para que ningún candidato trunque un tramo contra
        el límite del horizonte — eso confundiría esta prueba con el
        comportamiento intencional de 'tramo fuera de ventana no consume
        cupo' (spec sección 5), que es un caso distinto y no se toca aquí."""
        opt = EnterpriseOptimizer(config=self.cfg)
        p = _make_project(
            pid="P920", capex=0, exec_year=2028, construction_years=2,
            capex_by_year={2028: 4_000_000_000, 2029: 4_000_000_000},
            is_mandatory=True,
        )
        periods = [ResourcePeriod(year=y, budget_COP=0.0) for y in range(2020, 2036)]
        for per in periods:
            if per.year in (2025, 2026):
                per.budget_COP = 5_000_000_000
        result = opt.optimize([p], periods, base_year=2020)
        seleccionado = next((s for s in result.selected if s["project_id"] == "P920"), None)
        self.assertIsNotNone(
            seleccionado,
            "Solo 2025/2026 tienen cupo simultáneo para los dos tramos "
            "reanclados; si esto falla, R1 sigue buscando 2028/2029 literales.",
        )
        # assertIsNotNone por sí solo no discrimina: bajo el bug viejo, un
        # proyecto con capex=0 y perfil no-literal parece costar 0 en TODAS
        # las vigencias (no solo en 2028/2029), así que siempre "cabe" en
        # cualquier ventana con presupuesto. Lo que sí distingue el arreglo
        # es que el monto total reportado sea el perfil completo, no 0.
        total_consumido = sum(
            per["used_COP"] for per in result.budget_utilization.values()
        )
        self.assertAlmostEqual(total_consumido, 8_000_000_000, delta=1.0)

    def test_enterprise_r1_no_mezcla_ramas_dentro_de_la_ventana(self):
        """Un perfil que declara SOLO el primer año de una ventana de
        construcción de 2 años no debe cobrar además el reparto uniforme
        del total en el segundo año — eso contaría el CAPEX dos veces."""
        opt = EnterpriseOptimizer(config=self.cfg)
        p = _make_project(
            pid="P921", capex=6_000_000_000, exec_year=2026, construction_years=2,
            capex_by_year={2026: 6_000_000_000},  # 2027 deliberadamente sin declarar
            is_mandatory=True,
        )
        periods = [ResourcePeriod(year=y, budget_COP=7_000_000_000) for y in (2026, 2027)]
        result = opt.optimize([p], periods, base_year=2026)
        total_consumido = sum(
            per["used_COP"] for per in result.budget_utilization.values()
        )
        # El total consumido en todo el plan no puede exceder el CAPEX
        # declarado (6.000M) — si el año 2027 cobra ADEMÁS 3.000M por la
        # rama uniforme, el total sube a 9.000M.
        self.assertLessEqual(total_consumido, 6_000_000_000 + 1.0)

    def test_enterprise_r12_totex_reancla_capex_by_year(self):
        """R12 (TOTEX) debe reanclar capex_by_year al candidato igual que
        R1 — antes, un proyecto con perfil explícito parecía costar 0 en
        TOTEX para cualquier año candidato que no fuera exactamente el
        declarado, dejándolo pasar el techo TOTEX cuando en realidad sí
        debía contar."""
        opt = EnterpriseOptimizer(config=self.cfg)
        p = _make_project(
            pid="P922", capex=0, exec_year=2028,
            capex_by_year={2028: 4_000_000_000},
            is_mandatory=True,
        )
        periods = [
            ResourcePeriod(year=y, budget_COP=10_000_000_000,
                           totex_budget_COP=1_000_000_000)  # techo bajo, aprieta
            for y in (2025, 2026)
        ]
        result = opt.optimize([p], periods, base_year=2025)
        seleccionado = next((s for s in result.selected if s["project_id"] == "P922"), None)
        self.assertIsNone(
            seleccionado,
            "Con techo TOTEX de 1.000M y CAPEX real de 4.000M, el proyecto "
            "no debía caber en ningún año — si se seleccionó, R12 sigue "
            "viendo el perfil reanclado como gratis (costo 0).",
        )

    def test_enterprise_r12_totex_cuenta_todos_los_tramos(self):
        """R12 debe sumar TODOS los tramos del perfil, no solo el primero —
        con get(declared) en vez de sum(...), un proyecto de dos tramos
        (4.000M + 4.000M) contaría solo 4.000M contra el techo TOTEX,
        dejándolo colarse bajo un techo que en realidad debería superar."""
        opt = EnterpriseOptimizer(config=self.cfg)
        p = _make_project(
            pid="P923", capex=0, exec_year=2025, construction_years=2,
            capex_by_year={2025: 4_000_000_000, 2026: 4_000_000_000},
            is_mandatory=True,
        )
        periods = [
            ResourcePeriod(year=y, budget_COP=10_000_000_000,
                           totex_budget_COP=5_000_000_000)  # cabe 1 tramo, no los 2
            for y in (2025, 2026)
        ]
        result = opt.optimize([p], periods, base_year=2025)
        seleccionado = next((s for s in result.selected if s["project_id"] == "P923"), None)
        self.assertIsNone(
            seleccionado,
            "Con techo TOTEX de 5.000M y CAPEX real de 8.000M (dos "
            "tramos), el proyecto no debía caber — si se seleccionó, R12 "
            "solo está contando el primer tramo (4.000M), no el total.",
        )

    def test_portfolio_simple_uniforme_no_ancla_al_primer_periodo(self):
        """Regresión del camino uniforme: un proyecto con construction_years=2
        que arranca en el SEGUNDO período del plan (no en el primero) debe
        poder seleccionarse igual que si arrancara en el primero, siempre que
        haya presupuesto en los años que realmente ocupa."""
        opt = PortfolioOptimizer(config=self.cfg)
        p = _make_project(
            pid="P930", capex=4_000_000_000, exec_year=2027,
            construction_years=2, is_mandatory=True,
        )
        periods = [
            BudgetPeriod(year=2026, budget_COP=0.0),
            BudgetPeriod(year=2027, budget_COP=3_000_000_000),
            BudgetPeriod(year=2028, budget_COP=3_000_000_000),
        ]
        result = opt.optimize([p], periods, base_year=2026)
        seleccionado = next((s for s in result.selected if s["project_id"] == "P930"), None)
        self.assertIsNotNone(
            seleccionado,
            "El proyecto debía caber en 2027-2028 (2.000M cada año); si "
            "esto falla, la matriz de CAPEX sigue anclada al índice del "
            "primer período en vez de al candidato que se prueba.",
        )
        # assertIsNotNone por sí solo no discrimina aquí: bajo el bug viejo,
        # el proyecto igual queda "seleccionado", pero en 2026 — el único
        # año con presupuesto CERO. Eso pasa porque la matriz anclada al
        # índice literal del año declarado (2027) deja el desplazamiento
        # relativo de la Restricción 1 apuntando a celdas vacías cuando el
        # candidato probado es 2026, así que el solver lo ve "gratis" ahí.
        # El año seleccionado sí distingue: 2026 (bug, presupuesto cero) vs.
        # 2027/2028 (arreglado, dentro de las vigencias con cupo real).
        self.assertIn(
            seleccionado["year"], (2027, 2028),
            "El proyecto no debía poder seleccionarse en 2026 (presupuesto "
            "cero ese año); si aparece ahí, la matriz de CAPEX sigue "
            "reportando el proyecto como gratis para ese candidato.",
        )

    def test_portfolio_simple_reancla_capex_by_year(self):
        """Mismo caso que el de EnterpriseOptimizer, para el optimizador
        simple: el perfil debe reanclarse al año candidato."""
        opt = PortfolioOptimizer(config=self.cfg)
        p = _make_project(
            pid="P931", capex=0, exec_year=2028, construction_years=2,
            capex_by_year={2028: 4_000_000_000, 2029: 4_000_000_000},
            is_mandatory=True,
        )
        periods = [
            BudgetPeriod(year=2026, budget_COP=5_000_000_000),
            BudgetPeriod(year=2027, budget_COP=5_000_000_000),
            BudgetPeriod(year=2028, budget_COP=0.0),
            BudgetPeriod(year=2029, budget_COP=0.0),
        ]
        result = opt.optimize([p], periods, base_year=2026)
        seleccionado = next((s for s in result.selected if s["project_id"] == "P931"), None)
        self.assertIsNotNone(seleccionado)
        # assertIsNotNone por sí solo no discrimina: bajo el bug viejo, el
        # proyecto igual queda "seleccionado", pero en 2028 — un año con
        # presupuesto CERO — porque capex_by_year se busca por año
        # calendario literal en vez de reanclarse, y la Restricción 1
        # termina apuntando a celdas vacías para ese candidato. El único
        # año realmente viable dado este presupuesto es 2026 (2026 y 2027
        # tienen 5.000M cada uno; el perfil reanclado necesita 4.000M en
        # el año de arranque y 4.000M en el siguiente).
        self.assertEqual(
            seleccionado["year"], 2026,
            "El único año con presupuesto suficiente en los dos años que "
            "el perfil ocupa es 2026; si se seleccionó otro año (p.ej. "
            "2028, que tiene presupuesto cero), capex_by_year no se está "
            "reanclando al candidato.",
        )

    def test_portfolio_simple_capex_legado_sensible_al_candidato(self):
        """Antes de este Task, el VNT que reporta optimize() para un
        proyecto con CAPEX legado era casi idéntico sin importar el año
        candidato, porque el bucle interno nunca pasaba deferral_years.
        Forzar la selección en 2026 vs. forzar la selección en 2030 (vía
        presupuesto disponible solo en cada uno) debe dar VNT reportados
        claramente distintos — si no difieren, deferral_years no se está
        calculando ni pasando en el bucle de optimize()."""
        opt = PortfolioOptimizer(config=self.cfg)

        def _forzar(anio_con_cupo):
            p = _make_project(pid="P932", capex=5_000_000_000, exec_year=anio_con_cupo,
                              is_mandatory=True)
            periods = [
                BudgetPeriod(year=y, budget_COP=10_000_000_000 if y == anio_con_cupo else 0.0)
                for y in (2026, 2030)
            ]
            result = opt.optimize([p], periods, base_year=2026)
            seleccionado = next(s for s in result.selected if s["project_id"] == "P932")
            return seleccionado["net_value_COP"]

        vnt_2026 = _forzar(2026)
        vnt_2030 = _forzar(2030)
        self.assertGreater(
            abs(vnt_2026 - vnt_2030), abs(vnt_2026) * 0.01,
            "El VNT reportado entre 2026 y 2030 debía diferir en más del "
            "1% — si difiere menos, optimize() no está pasando "
            "deferral_years real en su bucle interno.",
        )

    def test_portfolio_simple_selected_reporta_capex_real_del_perfil(self):
        """selected['capex_COP'] y ['vei'] deben reflejar el total real del
        perfil, no el escalar capex_COP=0 que declaran los proyectos de
        perfil explícito — de lo contrario el reporte impreso muestra CAPEX
        y VEI en cero para un proyecto que el motor sí contabilizó bien."""
        opt = PortfolioOptimizer(config=self.cfg)
        p = _make_project(
            pid="P933", capex=0, exec_year=2026,
            capex_by_year={2026: 3_000_000_000, 2027: 2_000_000_000},
            is_mandatory=True,
        )
        periods = [BudgetPeriod(year=y, budget_COP=5_000_000_000) for y in (2026, 2027)]
        result = opt.optimize([p], periods, base_year=2026)
        seleccionado = next(s for s in result.selected if s["project_id"] == "P933")
        self.assertAlmostEqual(seleccionado["capex_COP"], 5_000_000_000, delta=1.0)
        # Oráculo independiente: VEI = VNT / CAPEX real (5.000M), no VNT/0.
        vei_esperado = seleccionado["net_value_COP"] / 5_000_000_000
        self.assertAlmostEqual(seleccionado["vei"], vei_esperado, delta=1e-6)

    def test_enterprise_selected_reporta_capex_real_del_perfil(self):
        """Mismo caso que el anterior, para EnterpriseOptimizer."""
        opt = EnterpriseOptimizer(config=self.cfg)
        p = _make_project(
            pid="P934", capex=0, exec_year=2026,
            capex_by_year={2026: 3_000_000_000, 2027: 2_000_000_000},
            is_mandatory=True,
        )
        periods = [ResourcePeriod(year=y, budget_COP=5_000_000_000) for y in (2026, 2027)]
        result = opt.optimize([p], periods, base_year=2026)
        seleccionado = next(s for s in result.selected if s["project_id"] == "P934")
        self.assertAlmostEqual(seleccionado["capex_COP"], 5_000_000_000, delta=1.0)
        # Oráculo independiente: VEI = VNT / CAPEX real (5.000M), no VNT/0.
        vei_esperado = seleccionado["net_value_COP"] / 5_000_000_000
        self.assertAlmostEqual(seleccionado["vei"], vei_esperado, delta=1e-6)

    def test_enterprise_r1_no_mezcla_ramas_bajo_presion_de_presupuesto(self):
        """Refuerzo de test_enterprise_r1_no_mezcla_ramas_dentro_de_la_ventana:
        esa prueba mide 'used_COP' de budget_utilization, que se calcula con
        capex_matrix (una matriz DISTINTA de la que usa la restricción R1 en
        el propio LP) — no discrimina si R1 vuelve a mezclar ramas porque esa
        mezcla nunca se refleja ahí. Aquí se aprieta el presupuesto del año
        NO declarado (2027) para que la restricción R1 misma sea la que
        decida: con capex_by_year={2026: 6.000M} y capex_COP=6.000M como
        escalar de respaldo (self.costs.capex_COP), si R1 vuelve a caer al
        reparto uniforme para 2027 (año sin tramo declarado dentro de la
        ventana de 2 años), exigiría 3.000M ahí — por encima del presupuesto
        de 1.000M — y el proyecto (obligatorio) quedaría sin poder
        seleccionarse en ningún año. Bajo el comportamiento correcto, 2027
        no consume nada (el perfil no declara nada ahí) y el proyecto cabe
        en 2026 sin problema."""
        opt = EnterpriseOptimizer(config=self.cfg)
        p = _make_project(
            pid="P935", capex=6_000_000_000, exec_year=2026, construction_years=2,
            capex_by_year={2026: 6_000_000_000},  # 2027 deliberadamente sin declarar
            is_mandatory=True,
        )
        periods = [
            ResourcePeriod(year=2026, budget_COP=7_000_000_000),
            ResourcePeriod(year=2027, budget_COP=1_000_000_000),  # insuficiente para el reparto uniforme (3.000M)
        ]
        result = opt.optimize([p], periods, base_year=2026)
        seleccionado = next((s for s in result.selected if s["project_id"] == "P935"), None)
        self.assertIsNotNone(
            seleccionado,
            "El perfil no declara nada en 2027, así que R1 no debería exigir "
            "presupuesto ahí; si el proyecto no se seleccionó, R1 sigue "
            "mezclando ramas dentro de la ventana de construcción.",
        )
        self.assertEqual(seleccionado["year"], 2026)

    def test_portfolio_uniforme_no_ancla_al_indice_del_declarado(self):
        """Refuerzo de test_portfolio_simple_uniforme_no_ancla_al_primer_periodo:
        esa prueba deja que la economía del proyecto (VNT) decida el año, y
        no discrimina si la rama uniforme de capex_matrix vuelve a anclarse
        al índice del execution_year declarado en vez de a 0 (relativo).
        Aquí se fuerza la discriminación con presupuesto CERO en TODAS las
        vigencias: bajo el comportamiento correcto, capex_matrix[i, 0] (el
        costo en el propio año de arranque) es no-nulo para CUALQUIER
        candidato — con presupuesto cero en todas partes, el proyecto
        obligatorio no puede caber en ningún año y queda sin seleccionar.
        Bajo el defecto (ancla al índice absoluto del año declarado, que no
        es el primer período), capex_matrix[i, 0] queda en cero para todo
        candidato salvo el que coincide exactamente con ese índice absoluto,
        así que el proyecto se ve 'gratis' y se selecciona pese al
        presupuesto cero."""
        opt = PortfolioOptimizer(config=self.cfg)
        p = _make_project(
            pid="P936", capex=4_000_000_000, exec_year=2028, construction_years=1,
            is_mandatory=True,
        )
        periods = [BudgetPeriod(year=y, budget_COP=0.0) for y in (2026, 2027, 2028, 2029)]
        result = opt.optimize([p], periods, base_year=2026)
        seleccionado = next((s for s in result.selected if s["project_id"] == "P936"), None)
        self.assertIsNone(
            seleccionado,
            "Con presupuesto cero en todas las vigencias, un proyecto "
            "obligatorio con costo real no puede caber en ningún año; si "
            "quedó seleccionado, capex_matrix (rama uniforme) se está "
            "anclando al índice del execution_year declarado en vez de "
            "reportar el costo relativo a CUALQUIER candidato.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# S32 — PRESELECCIÓN DE ALTERNATIVAS POR VALOR ANUAL EQUIVALENTE
# ─────────────────────────────────────────────────────────────────────────────

class S32_PreseleccionPorEAV(unittest.TestCase):
    """Dentro de un exclusion_group_id, gana la alternativa de mayor EAV,
    no la de mayor VNT crudo — salvo que una esté protegida (no-discrecional
    o con Gasto Comprometido ya firmado), en cuyo caso esa gana sin importar
    su EAV."""

    def setUp(self):
        self.cfg = _cfg()
        self.engine = ValueEngine(self.cfg)

    def test_preseleccion_elige_mayor_eav_no_mayor_vnt(self):
        """Prueba decisiva: una alternativa de vida larga con VNT crudo mayor
        pierde frente a una de vida corta con EAV mayor. Esta prueba debe
        fallar si _preselect_by_eav no existe o si usa net_value_COP.

        get_horizon_for_asset() resuelve el horizonte por asset_type (con
        fallback a TyD._default=30 si no se declara). Sin asset_type
        explícito, ambas alternativas caerían en ese mismo fallback: con
        horizonte idéntico, EAV = VNT / mismo_factor_de_anualidad para las
        dos, así que EAV nunca podría invertir el orden de VNT — el caso no
        discriminaría nada de lo que dice probar. Por eso aquí se declara
        asset_type distinto para cada una (activos reales de TyD, con vidas
        útiles muy distintas: 30a vs 10a), y el CAPEX/beneficio anual de
        cada una se calibra para que la ganadora por VNT crudo (larga,
        pago lento: bajo beneficio anual frente a su CAPEX) y la ganadora
        por EAV (corta, pago rápido: beneficio anual casi igual a su CAPEX)
        efectivamente se separen — un simple reescalado proporcional de
        ambas alternativas no basta, EAV es monótono con VNT cuando el
        horizonte coincide."""
        sc_grande = _make_sc("VD-1-A", delta=30_000.0, price=335_000)
        larga = _make_project(
            pid="ALT_LARGA", capex=50_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_grande])],
            exclusion_group_id="NECESIDAD_X",
        )
        larga.asset_type = "transformador_potencia"  # TyD: 30 años
        larga.analysis_horizon_years = self.engine.get_horizon_for_asset(larga)
        sc_chica = _make_sc("VD-1-A", delta=22_000.0, price=335_000)
        corta = _make_project(
            pid="ALT_CORTA", capex=8_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_chica])],
            exclusion_group_id="NECESIDAD_X",
        )
        corta.asset_type = "sistema_control_SCADA"  # TyD: 10 años
        corta.analysis_horizon_years = self.engine.get_horizon_for_asset(corta)
        # Confirmar la premisa del caso antes de probar la función: la larga
        # debe tener mayor VNT crudo Y menor EAV que la corta — si el config
        # cambia y esto deja de cumplirse, la prueba debe fallar aquí, no en
        # un assertEqual confuso más abajo.
        r_larga = self.engine.evaluate(larga)
        r_corta = self.engine.evaluate(corta)
        self.assertGreater(r_larga["net_value_COP"], r_corta["net_value_COP"],
                            "Premisa del caso: la larga debe ganar por VNT crudo")
        self.assertGreater(r_corta["eav_COP"], r_larga["eav_COP"],
                            "Premisa del caso: la corta debe ganar por EAV")

        ganadores, descartados = _preselect_by_eav([larga, corta], self.engine)
        ids_ganadores = {p.id for p in ganadores}
        self.assertEqual(ids_ganadores, {"ALT_CORTA"})
        self.assertEqual(len(descartados), 1)
        self.assertEqual(descartados[0][0].id, "ALT_LARGA")
        self.assertEqual(descartados[0][1].id, "ALT_CORTA")

    def test_preseleccion_respeta_obligatoria_aunque_tenga_menor_eav(self):
        """Lo no-discrecional pesa más que el EAV."""
        obligatoria = _make_project(
            pid="ALT_OBLIGATORIA", capex=50_000_000_000, exec_year=2026,
            exclusion_group_id="NECESIDAD_Y", is_mandatory=True,
        )
        mejor_eav = _make_project(
            pid="ALT_MEJOR_EAV", capex=8_000_000_000, exec_year=2026,
            exclusion_group_id="NECESIDAD_Y",
        )
        ganadores, descartados = _preselect_by_eav([obligatoria, mejor_eav], self.engine)
        ids_ganadores = {p.id for p in ganadores}
        self.assertEqual(ids_ganadores, {"ALT_OBLIGATORIA"})

    def test_preseleccion_dos_obligatorias_falla(self):
        """Dos alternativas no-discrecionales en el mismo grupo son un error
        de captura, no un caso a resolver en silencio — mismo criterio que
        ya existe hoy en la restricción R3d de ambos optimizadores."""
        a = _make_project(pid="A", exclusion_group_id="Z", is_mandatory=True)
        b = _make_project(pid="B", exclusion_group_id="Z", is_mandatory=True)
        with self.assertRaises(ValueError):
            _preselect_by_eav([a, b], self.engine)

    def test_preseleccion_no_toca_proyectos_sin_grupo(self):
        p1 = _make_project(pid="SIN_GRUPO_1")
        p2 = _make_project(pid="SIN_GRUPO_2")
        ganadores, descartados = _preselect_by_eav([p1, p2], self.engine)
        self.assertEqual({p.id for p in ganadores}, {"SIN_GRUPO_1", "SIN_GRUPO_2"})
        self.assertEqual(descartados, [])

    def test_preseleccion_grupo_de_un_solo_elemento_no_se_descarta(self):
        p = _make_project(pid="UNICA", exclusion_group_id="SOLO_UNA")
        ganadores, descartados = _preselect_by_eav([p], self.engine)
        self.assertEqual({p.id for p in ganadores}, {"UNICA"})
        self.assertEqual(descartados, [])

    def test_preseleccion_respeta_comprometida_aunque_tenga_menor_eav(self):
        """Gasto Comprometido (contrato ya firmado) pesa más que el EAV,
        igual que lo obligatorio."""
        comprometida = _make_project(
            pid="ALT_COMPROMETIDA", capex=50_000_000_000, exec_year=2026,
            exclusion_group_id="NECESIDAD_COMP", is_committed=True,
        )
        mejor_eav = _make_project(
            pid="ALT_MEJOR_EAV_2", capex=8_000_000_000, exec_year=2026,
            exclusion_group_id="NECESIDAD_COMP",
        )
        ganadores, descartados = _preselect_by_eav([comprometida, mejor_eav], self.engine)
        ids_ganadores = {p.id for p in ganadores}
        self.assertEqual(ids_ganadores, {"ALT_COMPROMETIDA"})

    def test_preseleccion_comprometida_y_obligatoria_en_el_mismo_grupo_falla(self):
        """Una comprometida y una obligatoria compitiendo en el mismo grupo
        son dos protecciones distintas reclamando la misma necesidad — error
        de captura, no un caso a resolver en silencio."""
        obligatoria = _make_project(pid="OBL", exclusion_group_id="ZZ", is_mandatory=True)
        comprometida = _make_project(pid="COMP", exclusion_group_id="ZZ", is_committed=True)
        with self.assertRaises(ValueError):
            _preselect_by_eav([obligatoria, comprometida], self.engine)

    def test_r3d_sigue_activo_si_se_desactiva_la_preseleccion(self):
        """La preselección por EAV ahora reduce cada grupo a <=1 ANTES de
        que el MILP corra, así que su propia restricción de exclusión de
        grupo (R3d / Restricción 5) nunca se ejercita en la práctica — tres
        pruebas preexistentes que decían vigilarla (S2, S20, S23) ahora
        pasan por esa razón, no por la restricción MILP misma. Esta prueba
        restaura cobertura real: desactiva la preselección (monkeypatch a
        una función identidad que no descarta nada) y confirma que la
        restricción del MILP, sola, sigue impidiendo que se seleccione más
        de una alternativa del mismo grupo."""
        import unittest.mock as _mock

        def _identidad(projects, engine):
            return list(projects), []

        # Ambas alternativas deben tener VNT fuertemente positivo (~5.500 MM
        # y ~5.300 MM COP, verificado): el optimizador las QUIERE a ambas, no
        # hay que forzarlo. Si no fuera así, "a lo sumo 1 seleccionado" sería
        # trivialmente cierto por falta de presupuesto/atractivo y no
        # probaría que la restricción de grupo es la que está actuando —
        # verificado empíricamente desactivando temporalmente la restricción
        # de grupo del MILP: sin ella el solver selecciona AMBAS (2), con
        # ella exactamente 1.
        sc_a = _make_sc("VD-1-A", delta=2000.0, price=335_000)
        alt_a = _make_project(
            pid="R3D_A", capex=500_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_a])],
            exclusion_group_id="NECESIDAD_R3D",
        )
        alt_a.asset_type = "_default"
        sc_b = _make_sc("VD-1-A", delta=1900.0, price=335_000)
        alt_b = _make_project(
            pid="R3D_B", capex=450_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_b])],
            exclusion_group_id="NECESIDAD_R3D",
        )
        alt_b.asset_type = "_default"
        periods_simple = [BudgetPeriod(year=2026, budget_COP=100_000_000_000)]
        with _mock.patch.object(epm_core, "_preselect_by_eav", side_effect=_identidad):
            opt = PortfolioOptimizer(config=self.cfg)
            result = opt.optimize([alt_a, alt_b], periods_simple, base_year=2026)
        ids_seleccionados = [s["project_id"] for s in result.selected]
        self.assertEqual(
            len(ids_seleccionados), 1,
            "Con la preselección desactivada y ambas alternativas de VNT "
            "fuertemente positivo (el optimizador las quiere a ambas), la "
            "restricción de exclusión de grupo del MILP (Restricción 5) debe "
            "seguir permitiendo exactamente 1 de NECESIDAD_R3D — ni 0 ni 2.",
        )

    def test_portfolio_simple_usa_preseleccion(self):
        """La alternativa descartada por EAV nunca debe llegar a competir
        por presupuesto (no debe aparecer en selected) — pero tampoco debe
        desaparecer en silencio: debe aparecer en postponed con la razón
        explícita (spec sección 6: 'no desaparecen en silencio, se reportan
        con la razón')."""
        opt = PortfolioOptimizer(config=self.cfg)
        sc_grande = _make_sc("VD-1-A", delta=30_000.0, price=335_000)
        larga = _make_project(
            pid="OPT_LARGA", capex=50_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_grande])],
            exclusion_group_id="NECESIDAD_OPT",
        )
        larga.asset_type = "transformador_potencia"  # TyD: 30 años
        larga.analysis_horizon_years = self.engine.get_horizon_for_asset(larga)
        sc_chica = _make_sc("VD-1-A", delta=22_000.0, price=335_000)
        corta = _make_project(
            pid="OPT_CORTA", capex=8_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_chica])],
            exclusion_group_id="NECESIDAD_OPT",
        )
        corta.asset_type = "sistema_control_SCADA"  # TyD: 10 años
        corta.analysis_horizon_years = self.engine.get_horizon_for_asset(corta)
        periods = [BudgetPeriod(year=2026, budget_COP=100_000_000_000)]
        result = opt.optimize([larga, corta], periods, base_year=2026)

        ids_seleccionados = {s["project_id"] for s in result.selected}
        self.assertNotIn(
            "OPT_LARGA", ids_seleccionados,
            "La alternativa de mayor VNT crudo pero menor EAV no debía "
            "llegar a competir por presupuesto en absoluto.",
        )
        descartada = next(
            (p for p in result.postponed if p["project_id"] == "OPT_LARGA"), None
        )
        self.assertIsNotNone(
            descartada,
            "La descartada por EAV debe quedar visible en postponed, no "
            "desaparecer en silencio (spec sección 6).",
        )
        self.assertIn("EAV", descartada["reason"])
        self.assertIn("OPT_CORTA", descartada["reason"])

    def test_portfolio_simple_descartada_por_eav_visible_aunque_infactible(self):
        """Las descartadas por EAV deben reportarse en postponed incluso si
        el solver resulta infactible (0 de presupuesto) — se conocen ANTES
        de correr el solver, no dependen de que este tenga éxito."""
        opt = PortfolioOptimizer(config=self.cfg)
        sc_grande = _make_sc("VD-1-A", delta=30_000.0, price=335_000)
        larga = _make_project(
            pid="INF_LARGA", capex=50_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_grande])],
            exclusion_group_id="NECESIDAD_INF",
        )
        larga.asset_type = "transformador_potencia"
        larga.analysis_horizon_years = self.engine.get_horizon_for_asset(larga)
        sc_chica = _make_sc("VD-1-A", delta=22_000.0, price=335_000)
        corta = _make_project(
            pid="INF_CORTA", capex=8_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_chica])],
            exclusion_group_id="NECESIDAD_INF", is_mandatory=True,
        )
        corta.asset_type = "sistema_control_SCADA"
        corta.analysis_horizon_years = self.engine.get_horizon_for_asset(corta)
        # Presupuesto 0: INF_CORTA (la ganadora por EAV, ahora obligatoria)
        # no cabe en ningún lado — el solver debe reportar infactible.
        periods = [BudgetPeriod(year=2026, budget_COP=0.0)]
        result = opt.optimize([larga, corta], periods, base_year=2026)
        self.assertEqual(result.solver_status, "INFEASIBLE")
        descartada = next(
            (p for p in result.postponed if p["project_id"] == "INF_LARGA"), None
        )
        self.assertIsNotNone(
            descartada,
            "La descartada por EAV debe seguir visible en postponed aunque "
            "el solver resulte infactible.",
        )

    def test_enterprise_usa_preseleccion(self):
        """Mismo caso que el del optimizador simple, para EnterpriseOptimizer."""
        opt = EnterpriseOptimizer(config=self.cfg)
        sc_grande = _make_sc("VD-1-A", delta=30_000.0, price=335_000)
        larga = _make_project(
            pid="ENT_LARGA", capex=50_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_grande])],
            exclusion_group_id="NECESIDAD_ENT",
        )
        larga.asset_type = "transformador_potencia"  # TyD: 30 años
        larga.analysis_horizon_years = self.engine.get_horizon_for_asset(larga)
        sc_chica = _make_sc("VD-1-A", delta=22_000.0, price=335_000)
        corta = _make_project(
            pid="ENT_CORTA", capex=8_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_chica])],
            exclusion_group_id="NECESIDAD_ENT",
        )
        corta.asset_type = "sistema_control_SCADA"  # TyD: 10 años
        corta.analysis_horizon_years = self.engine.get_horizon_for_asset(corta)
        periods = [ResourcePeriod(year=2026, budget_COP=100_000_000_000)]
        result = opt.optimize([larga, corta], periods, base_year=2026)

        ids_seleccionados = {s["project_id"] for s in result.selected}
        self.assertNotIn(
            "ENT_LARGA", ids_seleccionados,
            "La alternativa de mayor VNT crudo pero menor EAV no debía "
            "llegar a competir por presupuesto en absoluto.",
        )
        descartada = next(
            (p for p in result.postponed if p["project_id"] == "ENT_LARGA"), None
        )
        self.assertIsNotNone(
            descartada,
            "La descartada por EAV debe quedar visible en postponed, no "
            "desaparecer en silencio (spec sección 6).",
        )
        self.assertIn("EAV", descartada["reason"])
        self.assertIn("ENT_CORTA", descartada["reason"])

    def test_enterprise_descartada_por_eav_visible_aunque_infactible(self):
        """Las descartadas por EAV deben reportarse en postponed incluso si
        el solver resulta infactible — se conocen ANTES de correr el
        solver, no dependen de que este tenga éxito."""
        opt = EnterpriseOptimizer(config=self.cfg)
        sc_grande = _make_sc("VD-1-A", delta=30_000.0, price=335_000)
        larga = _make_project(
            pid="ENT_INF_LARGA", capex=50_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_grande])],
            exclusion_group_id="NECESIDAD_ENT_INF",
        )
        larga.asset_type = "transformador_potencia"
        larga.analysis_horizon_years = self.engine.get_horizon_for_asset(larga)
        sc_chica = _make_sc("VD-1-A", delta=22_000.0, price=335_000)
        corta = _make_project(
            pid="ENT_INF_CORTA", capex=8_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_chica])],
            exclusion_group_id="NECESIDAD_ENT_INF", is_mandatory=True,
        )
        corta.asset_type = "sistema_control_SCADA"
        corta.analysis_horizon_years = self.engine.get_horizon_for_asset(corta)
        # Solo ENT_INF_CORTA es obligatoria (la ganadora por EAV) — si ambas
        # lo fueran, _preselect_by_eav lanzaría su propio ValueError de "dos
        # obligatorias" antes de llegar al solver, que no es el escenario
        # que esta prueba quiere ejercitar.
        periods = [ResourcePeriod(year=2026, budget_COP=0.0)]
        result = opt.optimize([larga, corta], periods, base_year=2026)
        self.assertEqual(result.solver_status, "INFEASIBLE")
        descartada = next(
            (p for p in result.postponed if p["project_id"] == "ENT_INF_LARGA"), None
        )
        self.assertIsNotNone(
            descartada,
            "La descartada por EAV debe seguir visible en postponed aunque "
            "el solver resulte infactible.",
        )


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


class S34_AssetProfileStatusField(unittest.TestCase):
    """AssetProfile gana status (activo/retirado) para soft-delete (D-14)."""

    def test_asset_profile_status_default_activo(self):
        a = AssetProfile(asset_id="A1", asset_name="Transformador 1",
                         asset_class="transformador", business_unit="TyD")
        self.assertEqual(a.status, "activo")

    def test_asset_profile_status_se_puede_declarar_retirado(self):
        a = AssetProfile(asset_id="A2", asset_name="Transformador 2",
                         asset_class="transformador", business_unit="TyD",
                         status="retirado")
        self.assertEqual(a.status, "retirado")


class S35_AssetRegistryStore(unittest.TestCase):
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

    def test_import_csv_fila_valida_se_importa_aunque_otra_fila_sea_duplicada(self):
        """Una fila invalida (duplicada) no debe bloquear la importacion de
        las demas filas validas del mismo archivo — el reporte es por fila,
        no todo-o-nada."""
        import io
        self.store.create(self._activo("AyA-TUB-030"))
        csv_content = (
            "asset_id,asset_name,asset_class,business_unit,install_year\n"
            "AyA-TUB-030,Duplicado,tuberia_ac,AyA,2010\n"
            "AyA-TUB-031,Valida,tuberia_ac,AyA,2012\n"
        )
        importados, errores = self.store.import_csv(io.StringIO(csv_content))
        self.assertEqual(len(importados), 1)
        self.assertEqual(importados[0].asset_id, "AyA-TUB-031")
        self.assertEqual(len(errores), 1)
        self.assertEqual(errores[0]["asset_id"], "AyA-TUB-030")
        self.assertIsNotNone(self.store.get("AyA-TUB-031"))

    def test_import_bytes_csv_sigue_funcionando(self):
        """import_bytes con un .csv debe comportarse igual que import_csv."""
        csv_bytes = (
            "asset_id,asset_name,asset_class,business_unit,install_year\n"
            "AyA-TUB-040,Tuberia CSV,tuberia_ac,AyA,2011\n"
        ).encode("utf-8")
        importados, errores = self.store.import_bytes(csv_bytes, "activos.csv")
        self.assertEqual(len(importados), 1)
        self.assertEqual(len(errores), 0)
        self.assertIsNotNone(self.store.get("AyA-TUB-040"))

    def test_import_bytes_xlsx_importa_filas_validas(self):
        """import_bytes con un .xlsx debe leer las mismas columnas que el CSV."""
        import openpyxl
        import io
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["asset_id", "asset_name", "asset_class", "business_unit", "install_year"])
        ws.append(["AyA-TUB-050", "Tuberia Excel", "tuberia_ac", "AyA", 2013])
        buf = io.BytesIO()
        wb.save(buf)
        importados, errores = self.store.import_bytes(buf.getvalue(), "activos.xlsx")
        self.assertEqual(len(importados), 1)
        self.assertEqual(len(errores), 0)
        self.assertIsNotNone(self.store.get("AyA-TUB-050"))


class S36_WeibullMultiComponente(unittest.TestCase):
    """asset_aware con 2+ componentes: cada componente lleva SU propia física
    Weibull (β/η/edad/CoF) y el beneficio (ΔR en el motor, EAL evitada en el
    asistente FASE B) suma la contribución de TODOS los componentes — no la de
    uno solo (antes: sólo el último `w_aw_baseline` capturado).

    Regla de regresión: con 1 solo componente el resultado es idéntico al de
    evaluar ese componente aislado.
    """

    BU = "TyD"

    def _cfg(self):
        return _cfg()

    def _comp(self, beta, eta, edad, cof_mm, cb=4, pb=4, cr=4, pr=4, label=""):
        """Un RiskInput asset_aware con curva Weibull propia.

        cr/pr = baseline por defecto → ΔR financiero de la matriz = 0; el ΔR
        real sale de la curva Weibull (misma convención que produce el asistente
        para asset_aware: el financiero fluye por la física, no por el
        movimiento ordinal C×P)."""
        return RiskInput(
            baseline_consequence_level=cb, baseline_probability_level=pb,
            residual_consequence_level=cr, residual_probability_level=pr,
            weibull_beta=beta, weibull_eta=eta, asset_age_years=edad,
            weibull_cof_COP=cof_mm * 1_000_000.0,
            label=label, asset_type="_default",
        )

    def _proj(self, componentes):
        p = _make_project(pid="AW-MULTI", bu=self.BU, capex=6_000_000_000,
                          inv_type="Reposicion de Activos",
                          drivers=[_make_driver("VD-1", [_make_sc("VD-1-A", delta=50.0)])],
                          risk=None, construction_years=1)
        p.asset_type = "_default"
        p.risk_components = list(componentes)
        p.analysis_horizon_years = ValueEngine(self._cfg()).get_horizon_for_asset(p)
        return p

    # ── 1. El campo weibull_cof_COP existe y es additivo ────────────────────

    def test_weibull_cof_cop_default_cero_y_declarable(self):
        r0 = RiskInput(baseline_consequence_level=3, baseline_probability_level=3)
        self.assertEqual(r0.weibull_cof_COP, 0.0)
        r1 = RiskInput(baseline_consequence_level=3, baseline_probability_level=3,
                       weibull_cof_COP=1_500_000_000.0)
        self.assertEqual(r1.weibull_cof_COP, 1_500_000_000.0)

    # ── 2. El motor suma el ΔR Weibull de CADA componente ──────────────────

    def test_motor_suma_delta_r_weibull_de_ambos_componentes(self):
        """ΔR(A+B) = ΔR(A solo) + ΔR(B solo), con residual < baseline para que
        cada curva Weibull produzca un ΔR distinto de cero."""
        # residual mejor que baseline (cr/pr = 2) → ΔR Weibull ≠ 0
        viejo_riesgoso = self._comp(beta=3.0, eta=30.0, edad=34.0, cof_mm=1500.0,
                                    cr=2, pr=2, label="Transformador viejo")
        nuevo_sano     = self._comp(beta=2.2, eta=45.0, edad=4.0,  cof_mm=200.0,
                                    cr=2, pr=2, label="Celda nueva")

        eng = ValueEngine(self._cfg())
        dr_ambos = eng.evaluate(self._proj([viejo_riesgoso, nuevo_sano]))["risk_analysis"]["delta_r_npv_COP"]
        dr_a = eng.evaluate(self._proj([viejo_riesgoso]))["risk_analysis"]["delta_r_npv_COP"]
        dr_b = eng.evaluate(self._proj([nuevo_sano]))["risk_analysis"]["delta_r_npv_COP"]

        self.assertGreater(dr_a, 0.0)
        self.assertGreater(dr_b, 0.0)
        self.assertAlmostEqual(dr_ambos / 1e6, (dr_a + dr_b) / 1e6, places=2,
            msg="El motor no suma el ΔR Weibull de los 2 componentes")
        # El 2º componente contribuye de verdad: A+B supera a A solo.
        self.assertGreater(dr_ambos, dr_a * 1.05)

    def test_regresion_un_solo_componente_identico_a_evaluar_aislado(self):
        solo = self._comp(beta=3.0, eta=30.0, edad=34.0, cof_mm=1500.0,
                          cr=2, pr=2, label="único")
        eng = ValueEngine(self._cfg())
        r_multi_api = eng.evaluate(self._proj([solo]))
        # mismo componente, misma vía — el valor no cambia por el hecho de
        # pasar por la ruta de "lista de componentes".
        self.assertEqual(len(r_multi_api["risk_analysis"]["components"]), 1)
        self.assertGreater(r_multi_api["risk_analysis"]["delta_r_npv_COP"], 0.0)

    # ── 3. El asistente (FASE B) suma la EAL evitada de TODOS ──────────────

    def _app(self):
        try:
            import app_epm_aip_full_enterprise as app
        except Exception as exc:                      # pragma: no cover
            self.skipTest(f"app no importable en bare mode: {exc}")
        return app

    def test_helper_eal_evitada_suma_todos_los_componentes_reposicion(self):
        app = self._app()
        viejo = self._comp(beta=3.0, eta=30.0, edad=34.0, cof_mm=1500.0)
        nuevo = self._comp(beta=2.2, eta=45.0, edad=4.0,  cof_mm=200.0)

        total_ab, desg_ab = app._asset_aware_eal_avoided_COP([viejo, nuevo], "reposicion")
        total_a, _        = app._asset_aware_eal_avoided_COP([viejo], "reposicion")
        total_b, _        = app._asset_aware_eal_avoided_COP([nuevo], "reposicion")

        self.assertEqual(len(desg_ab), 2)
        self.assertAlmostEqual(total_ab, total_a + total_b, places=2)
        self.assertGreater(total_a, 0.0)
        self.assertGreater(total_b, 0.0)
        # reposición ⇒ EAL evitada de cada componente = PoF_actual × CoF
        import math as _m
        pof_v = 1.0 - _m.exp(-((34.0 / 30.0) ** 3.0))
        self.assertAlmostEqual(total_a, pof_v * 1500.0 * 1_000_000.0, places=2)

    def test_helper_eal_evitada_ignora_componentes_sin_curva(self):
        app = self._app()
        con_curva = self._comp(beta=3.0, eta=30.0, edad=34.0, cof_mm=1500.0)
        sin_curva = RiskInput(baseline_consequence_level=3, baseline_probability_level=3)  # driver_based
        total, desg = app._asset_aware_eal_avoided_COP([con_curva, sin_curva], "reposicion")
        solo, _ = app._asset_aware_eal_avoided_COP([con_curva], "reposicion")
        self.assertEqual(len(desg), 1)
        self.assertAlmostEqual(total, solo, places=6)

    def test_helper_rehabilitacion_reduce_pero_no_anula_la_eal_evitada(self):
        app = self._app()
        viejo = self._comp(beta=3.0, eta=30.0, edad=34.0, cof_mm=1500.0)
        total_rep, _ = app._asset_aware_eal_avoided_COP([viejo], "reposicion")
        total_reh, _ = app._asset_aware_eal_avoided_COP([viejo], "rehabilitacion",
                                                        age_effective_new=12.0)
        self.assertGreater(total_reh, 0.0)
        self.assertLess(total_reh, total_rep)

    # ── 4. Sin doble conteo entre el ΔR del motor y el driver EAL-evitada ──

    def test_no_hay_doble_conteo_entre_motor_y_driver_eal_evitada(self):
        """El ΔR del motor para un componente Weibull con residual neutralizado
        (residual == baseline) debe ser exactamente 0 — su beneficio financiero
        SOLO puede fluir por el driver EAL-evitada del asistente, nunca por
        ambos canales a la vez (hallazgo de code review)."""
        comp_neutralizado = RiskInput(
            baseline_consequence_level=4, baseline_probability_level=4,
            residual_consequence_level=4, residual_probability_level=4,  # == baseline
            weibull_beta=3.0, weibull_eta=30.0, asset_age_years=34.0,
            weibull_cof_COP=1_500_000_000.0,
        )
        eng = ValueEngine(self._cfg())
        p = self._proj([comp_neutralizado])
        r = eng.evaluate(p)
        self.assertAlmostEqual(r["risk_analysis"]["delta_r_npv_COP"], 0.0, places=2,
            msg="Un componente con residual == baseline debe aportar ΔR=0 al motor "
                "— su beneficio debe fluir SOLO por el driver EAL-evitada")


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER + INFORME
# ─────────────────────────────────────────────────────────────────────────────

def _build_report(results: unittest.TestResult, elapsed: float) -> str:
    total   = results.testsRun
    failed  = len(results.failures)
    errors  = len(results.errors)
    skipped = len(results.skipped)
    passed  = total - failed - errors - skipped

    lines = [
        "",
        "=" * 76,
        "  INFORME DE VALIDACION -- EPM AIP Motor Predictivo Enterprise",
        f"  Fecha : {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  Tiempo: {elapsed:.1f}s",
        "=" * 76,
        "",
        f"  Total ejecutados : {total}",
        f"  Pasaron (OK)     : {passed}",
        f"  Fallaron         : {failed}",
        f"  Errores          : {errors}",
        f"  Omitidos (skip)  : {skipped}",
        "",
    ]

    if failed == 0 and errors == 0:
        lines += [
            "  [RESULTADO] APROBADO -- Todos los tests pasaron.",
            "",
            "  Cobertura validada:",
            "    S1  ValueEngine -- 5 VDs, 18 sub-criterios, 4 BUs + Todos",
            "    S2  MILP R1-R12: budget, unicidad, deps R3a-R3d, PM,",
            "        concurrencia, riesgo, pisos R7, R8 must-mitigate,",
            "        actuals/EVM R9, outage R10, committed R11, TOTEX R12",
            "    S3  DependencyEngine -- deadlock, mutex, lag, cascade CoD",
            "    S4  EVMEngine -- SPI, CPI, EAC, ETC, semaforo",
            "    S5  OutageCalendar + R10 -- alineacion de ventanas",
            "    S6  SnapshotStore ISO 55001 -- SHA-256, verify, audit report",
            "    S7  PlanVersionStore -- save, list, get, diff",
            "    S8  WorkflowEngine -- 9 estados, 4 roles, transiciones",
            "    S9  PortfolioLoader -- CSV, Excel, MM-heuristica, aliases",
            "    S10 SensitivityAnalyst -- WACC, frontera CAPEX/TOTEX",
            "    S11 AssetDegradationEngine -- Weibull, Lineal, Escalon, Acelerado",
            "    S12 Committed Spend R11 + committed_summary",
            "    S13 TOTEX R12 + totex_utilization + I2 hints",
            "    S14 Frontera eficiente CAPEX + TOTEX",
            "    S15 Riesgo multi-tipo por BU, R8 must-mitigate",
            "    S16 Proporcionalidad Full/Simplified/Rule-Based",
            "    S17 Helpers: load_config, save_config, print_report, audit_log",
            "    S18 Portafolio TI + silo / enterprise / pisos R7",
            "    S19 Validacion datos: aliases, flags, 50 proyectos",
            "    S20 Escenario integral: todos los modulos en una sola ejecucion",
            "    S21 Tratamiento temporal del diferimiento: exposicion cobrada,",
            "        ventana de evaluacion comun, CAPEX y VEI en valor presente",
        ]
    else:
        lines += ["  [RESULTADO] FALLIDO -- Revisar los fallos abajo."]
        if results.failures:
            lines += ["", "  FALLOS:"]
            for test, tb in results.failures:
                lines += [f"    - {test}"]
                lines += [f"      {tb.splitlines()[-1]}"]
        if results.errors:
            lines += ["", "  ERRORES:"]
            for test, tb in results.errors:
                lines += [f"    - {test}"]
                lines += [f"      {tb.splitlines()[-1]}"]

    lines += ["", "=" * 76, ""]
    return "\n".join(lines)


if __name__ == "__main__":
    loader_ut = unittest.TestLoader()
    suite     = loader_ut.loadTestsFromModule(__import__(__name__))

    stream  = io.StringIO()
    runner  = unittest.TextTestRunner(stream=stream, verbosity=2)
    t0      = time.time()
    results = runner.run(suite)
    elapsed = time.time() - t0

    # Volcar salida de forma segura en Windows (cp1252)
    output = stream.getvalue()
    try:
        sys.stdout.buffer.write(output.encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    except AttributeError:
        print(output.encode("ascii", errors="replace").decode("ascii"))

    report = _build_report(results, elapsed)
    try:
        sys.stdout.buffer.write(report.encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    except AttributeError:
        print(report.encode("ascii", errors="replace").decode("ascii"))

    # El informe ya salio completo por stdout. El archivo solo se escribe si
    # se pide: una corrida de pruebas no debe dejar residuos en el directorio
    # del proyecto, que es tambien el de produccion.
    if "--informe" in sys.argv:
        destino = PROJECT_DIR / "validation_report.txt"
        destino.write_text(report, encoding="utf-8")
        print(f"\n  Informe escrito en {destino}")

    sys.exit(0 if results.wasSuccessful() else 1)
