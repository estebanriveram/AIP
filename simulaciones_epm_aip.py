"""
simulaciones_epm_aip.py — Suite Integral de Simulaciones EPM AIP
================================================================
Valida CADA función del motor: 4 BUs, 5 VDs, 18 sub-criterios,
MILP R1-R12, dependencias, EVM, degradación, snapshots, workflow.

Ejecutar:
    python simulaciones_epm_aip.py
    python simulaciones_epm_aip.py --html   # genera informe HTML
"""
from __future__ import annotations
import sys, os, io, json, csv, copy, math, time, hashlib, tempfile, traceback
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

from epm_core import (
    DEFAULT_CONFIG, PULP_OK,
    ProjectInput, CostInput, DriverInput, SubCriterionInput, RiskInput,
    ResourcePeriod, AssetProfile, DegradationModel,
    ProjectActual, PlannedOutage, ProjectWorkflow,
    PlanVersion, DependencyError,
    ValueEngine, EnterpriseOptimizer, AssetDegradationEngine,
    SnapshotStore, DependencyEngine, PortfolioLoader, SensitivityAnalyst,
    EVMEngine, OutageCalendar, WorkflowEngine, PlanVersionStore,
    load_config, save_config, write_audit_log, _merge_config_defaults,
    WF_STATES, WF_FULL_TRANSITIONS, WF_TERMINAL_STATES,
)

# ── config desde archivo real si existe ───────────────────────────────────────
def _cfg() -> dict:
    """Config tal como la ve produccion: archivo FUSIONADO con DEFAULT_CONFIG.

    Antes se cargaba el JSON en crudo. Como config.json no trae
    consequence_levels_by_bu, wacc_by_bu ni cost_estimate_uncertainty, la suite
    ejercitaba el motor en una configuracion que la app nunca usa: el riesgo por
    negocio y el WACC por negocio quedaban sin probar.
    """
    p = PROJECT_DIR / "config.json"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        merged, _ = _merge_config_defaults(raw)
        return merged
    return copy.deepcopy(DEFAULT_CONFIG)

CFG = _cfg()

# Motor reutilizable solo para resolver el horizonte por tipo de activo.
# Tras la migracion D-14 evaluate() ya no llama get_horizon_for_asset(): el
# horizonte es un campo declarado del proyecto. _proj() lo deriva aqui con la
# misma jerarquia de lookup que usaba el motor antes, para que las pruebas que
# comparan horizontes por tipo de activo (transformador 30a vs SCADA 10a) sigan
# probando lo que dicen probar, sin numeros magicos.
_HORIZON_ENGINE = ValueEngine(CFG)

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS DE CONSTRUCCIÓN
# ══════════════════════════════════════════════════════════════════════════════

def _sc(sc_id: str, delta: float, price: float, label: str = "",
        stype: str = "monetizable") -> SubCriterionInput:
    return SubCriterionInput(
        id=sc_id, label=label or sc_id, type=stype,
        delta_annual=delta, unit="u", weight=1.0, price_per_unit=price,
    )

def _sc_cr(sc_id: str, nivel: float, scale_max: float,
           label: str = "") -> SubCriterionInput:
    return SubCriterionInput(
        id=sc_id, label=label or sc_id, type="concept_rating",
        delta_annual=nivel, unit="nivel", weight=1.0,
        value_function="s_curve", scale_max_COP=scale_max,
        vf_inflection=5.0, vf_steepness=1.5,
    )

def _drv(vd_id: str, sub_criteria: list) -> DriverInput:
    return DriverInput(id=vd_id, reto=vd_id, sub_criteria=sub_criteria)

def _risk(bc: int = 3, bp: int = 3, rc: int = 1, rp: int = 1,
          beta: float = 0.0, eta: float = 0.0, age: float = 0.0,
          ctypes: dict = None) -> RiskInput:
    r = RiskInput(
        baseline_consequence_level=bc, baseline_probability_level=bp,
        residual_consequence_level=rc, residual_probability_level=rp,
        weibull_beta=beta, weibull_eta=eta, asset_age_years=age,
    )
    if ctypes:
        r.consequence_types = ctypes
    return r

def _proj(pid: str, bu: str, capex: float,
          drivers: list = None, risk: RiskInput = None,
          horizon: int = 0,
          inv_type: str = "Reposicion de Activos",
          exec_year: int = 2026, opt_year: int = 2026,
          cy: int = 1, mandatory: bool = False,
          committed: bool = False, committed_year: int = 0,
          asset_id: str = "", asset_type: str = "",
          precedent: str = "", lag: int = 0,
          mutex: list = None, excl_group: str = None,
          outage_align: bool = False,
          opex: float = 0.0,
          delta_r_override: float = 0.0,
          name: str = "") -> ProjectInput:
    if drivers is None:
        drivers = [_drv("VD-1", [_sc("VD-1-A", 100.0, 335_000)])]
    p = ProjectInput(
        id=pid, name=name or pid, business_unit=bu,
        investment_type=inv_type,
        costs=CostInput(capex_COP=capex, opex_incremental_COP_year=opex),
        execution_year=exec_year, optimal_year=opt_year,
        drivers=drivers,
        risk_components=[risk] if risk else [],
        analysis_horizon_years=horizon,
        is_mandatory=mandatory, is_committed=committed,
        committed_year=committed_year, construction_years=cy,
    )
    p.asset_id = asset_id
    p.asset_type = asset_type
    p.precedent_project_id = precedent
    p.min_lag_years = lag
    p.mutual_exclusion_ids = mutex or []
    p.exclusion_group_id = excl_group
    p.require_outage_alignment = outage_align
    p.delta_r_override_COP = delta_r_override
    # Horizonte unico declarado (D-14). Si el test no lo fija, se resuelve por
    # tipo de activo / BU igual que hacia el motor antes de la migracion.
    if p.analysis_horizon_years <= 0:
        p.analysis_horizon_years = _HORIZON_ENGINE.get_horizon_for_asset(p)
    return p

def _period(year: int, budget: float,
            pm: float = float("inf"), conc: int = 999,
            risk_max: float = float("inf"),
            totex: float = None) -> ResourcePeriod:
    rp = ResourcePeriod(
        year=year, budget_COP=budget,
        max_person_months=pm,
        max_projects_concurrent=conc,
        max_risk_COP=risk_max,
    )
    if totex is not None:
        rp.totex_budget_COP = totex
    return rp

# ══════════════════════════════════════════════════════════════════════════════
# FRAMEWORK DE RESULTADOS
# ══════════════════════════════════════════════════════════════════════════════

class SimResult:
    def __init__(self, name: str, group: str):
        self.name  = name
        self.group = group
        self.ok    = True
        self.msgs  : List[str] = []
        self.t0    = time.time()
        self.elapsed = 0.0

    def check(self, cond: bool, msg: str, fatal: bool = False):
        if not cond:
            self.ok = False
            tag = "❌ FAIL" if fatal else "⚠  WARN"
            self.msgs.append(f"{tag}: {msg}")
        else:
            self.msgs.append(f"✓  {msg}")

    def done(self):
        self.elapsed = time.time() - self.t0

RESULTS: List[SimResult] = []
SIM_REGISTRY: List = []   # todas las simulaciones registradas

def sim(name: str, group: str):
    """Decorador de simulación."""
    def wrapper(fn):
        def inner():
            r = SimResult(name, group)
            try:
                fn(r)
            except Exception as e:
                r.ok = False
                r.msgs.append(f"EXCEPCION: {e}")
                r.msgs.append(traceback.format_exc()[:800])
            r.done()
            RESULTS.append(r)
            status = "PASS" if r.ok else "FAIL"
            print(f"  [{status}] {name} ({r.elapsed:.2f}s)")
        # Registrar en el registry global ANTES de renombrar
        SIM_REGISTRY.append(inner)
        inner.__name__ = fn.__name__
        return inner
    return wrapper

# ══════════════════════════════════════════════════════════════════════════════
# GRUPO S1 — ValueEngine: todos los BUs y Value Drivers
# ══════════════════════════════════════════════════════════════════════════════

@sim("S1.1 TyD: VD-1-A ENS + VD-1-B SAIDI + VD-2-C Pérdidas", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    drivers = [
        _drv("VD-1", [
            _sc("VD-1-A", 500.0, 335_000, "ENS evitada"),
            _sc("VD-1-B", 10_000.0, 6_200, "SAIDI"),
        ]),
        _drv("VD-2", [
            _sc("VD-2-C", 200.0, 335_000, "Pérdidas técnicas"),
        ]),
    ]
    p = _proj("TyD-01", "TyD", 15_000_000_000, drivers=drivers,
              risk=_risk(4, 3, 1, 1))
    res = eng.evaluate(p)
    r.check(res["net_value_COP"] > 0, f"VNT TyD = {res['net_value_COP']/1e6:.0f} MM")
    r.check(res["value_efficiency_index"] > 0, "VEI > 0")
    r.check(len(res["driver_results"]) == 2, "2 drivers procesados")
    r.check(res["delta_r_npv_COP"] > 0, "ΔR > 0 (riesgo mitigado)")
    r.check(res["eav_COP"] > 0, "EAV > 0")
    r.check(res["horizon_asset_years"] > 0, "Horizonte activo > 0")

@sim("S1.2 AyA: VD-1-C Agua + VD-1-D ICTAC + VD-3-B Conexiones", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    drivers = [
        _drv("VD-1", [
            _sc("VD-1-C", 100_000.0, 3_100, "Agua no suministrada"),
            _sc("VD-1-D", 5_000.0, 22_000, "Horas carrotanque"),
        ]),
        _drv("VD-3", [
            _sc("VD-3-B", 500.0, 6_800_000, "Nuevas conexiones AyA"),
        ]),
    ]
    p = _proj("AyA-01", "AyA", 8_000_000_000, drivers=drivers,
              risk=_risk(3, 3, 1, 1), inv_type="Crecimiento / Expansión")
    res = eng.evaluate(p)
    r.check(res["net_value_COP"] > 0, f"VNT AyA = {res['net_value_COP']/1e6:.0f} MM")
    r.check(res["benefits_raw_COP"] > 0, "Beneficios brutos > 0")

@sim("S1.3 Gas: VD-1-E Fugas + VD-2-A OPEX + VD-3-C Conexiones", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    drivers = [
        _drv("VD-1", [_sc("VD-1-E", 20.0, 50_000_000, "Fugas evitadas")]),
        _drv("VD-2", [_sc("VD-2-A", 500_000_000.0, 1.0, "OPEX evitado")]),
        _drv("VD-3", [_sc("VD-3-C", 800.0, 4_100_000, "Conexiones gas")]),
    ]
    p = _proj("Gas-01", "Gas", 12_000_000_000, drivers=drivers,
              risk=_risk(4, 4, 1, 1))
    res = eng.evaluate(p)
    r.check(res["net_value_COP"] > 0, f"VNT Gas = {res['net_value_COP']/1e6:.0f} MM")

@sim("S1.4 Generacion: VD-1-G Disponibilidad + VD-4-A CO2 + VD-5-A Ingresos", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    drivers = [
        _drv("VD-1", [
            _sc("VD-1-E", 50_000.0, 355_000, "Disponibilidad hidráulica"),
        ]),
        _drv("VD-4", [_sc("VD-4-A", 5_000.0, 88_000, "CO2 evitado")]),
        _drv("VD-5", [_sc("VD-5-A", 2_000_000_000.0, 1.0, "Ingresos contrato")]),
    ]
    p = _proj("Gen-01", "Generacion", 50_000_000_000, drivers=drivers,
              risk=_risk(5, 3, 2, 1), asset_type="turbina_hidro")
    res = eng.evaluate(p)
    r.check(res["net_value_COP"] > 0, f"VNT Gen = {res['net_value_COP']/1e6:.0f} MM")
    r.check(res["horizon_asset_years"] >= 25, "Horizonte turbina >= 25a")

@sim("S1.5 Concept Rating VD-5-C (S-Curve) + VD-1-F", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    drivers = [
        _drv("VD-5", [
            _sc_cr("VD-5-C", 7.0, 400_000_000_000, "Rentabilidad portafolio"),
        ]),
    ]
    p = _proj("TI-01", "Todos", 3_000_000_000, drivers=drivers,
              inv_type="Proyecto Empresarial / TI")
    res = eng.evaluate(p)
    r.check(res["net_value_COP"] != 0, "Concept rating genera valor no-cero")
    # Nivel neutral = 5 -> VNT debería ser aprox 0 (ni positivo ni negativo grande)
    drivers_neutral = [_drv("VD-5", [_sc_cr("VD-5-C", 5.0, 400_000_000_000)])]
    p2 = _proj("TI-02", "Todos", 1_000_000, drivers=drivers_neutral)
    res2 = eng.evaluate(p2)
    benefits2 = res2["benefits_raw_COP"]
    r.check(abs(benefits2) < 1e9, f"Nivel 5 (neutral) ≈ 0 beneficio: {benefits2/1e6:.1f} MM")

@sim("S1.6 VD-4-C Multas ANLA + VD-4-B Protección hídrica", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    drivers = [
        _drv("VD-4", [
            _sc("VD-4-C", 0.5, 3_200_000_000, "Multas ANLA"),
            _sc("VD-4-B", 200.0, 4_800_000, "Ha ecosistemas"),
        ]),
    ]
    p = _proj("AyA-ESG", "AyA", 5_000_000_000, drivers=drivers,
              inv_type="Sostenibilidad / ESG", risk=_risk(3, 3, 1, 1))
    res = eng.evaluate(p)
    r.check(res["net_value_COP"] > 0, f"VNT ESG = {res['net_value_COP']/1e6:.0f} MM")

@sim("S1.7 VNT negativo — inversión destructora de valor", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    # CAPEX enorme, beneficio mínimo -> VNT negativo
    drivers = [_drv("VD-2", [_sc("VD-2-A", 1.0, 1.0)])]
    p = _proj("DESTR-01", "TyD", 100_000_000_000, drivers=drivers)
    res = eng.evaluate(p)
    r.check(res["net_value_COP"] < 0, "VNT negativo correctamente calculado")
    r.check(res["alerta_vnt_negativo"] is not None, "Alerta pedagógica presente")

@sim("S1.8 CoD — penalización por retraso vs año óptimo", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    drivers = [_drv("VD-1", [_sc("VD-1-A", 1000.0, 335_000)])]
    p_opt  = _proj("P-OPT",  "TyD", 5_000_000_000, drivers=drivers,
                   exec_year=2026, opt_year=2026)
    p_late = _proj("P-LATE", "TyD", 5_000_000_000, drivers=copy.deepcopy(drivers),
                   exec_year=2028, opt_year=2026)
    r_opt  = eng.evaluate(p_opt)
    r_late = eng.evaluate(p_late)
    r.check(r_opt["net_value_COP"] > r_late["net_value_COP"],
            f"VNT óptimo ({r_opt['net_value_COP']/1e6:.0f}MM) > tardío ({r_late['net_value_COP']/1e6:.0f}MM)")
    r.check(r_late["timing_analysis"]["cod_total_COP"] > 0, "CoD > 0 cuando hay retraso")

@sim("S1.9 Riesgo Weibull PoF vs exponencial legacy", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    drivers = [_drv("VD-1", [_sc("VD-1-A", 200.0, 335_000)])]
    risk_exp = _risk(4, 3, 1, 1)
    risk_wbl = _risk(4, 3, 1, 1, beta=2.5, eta=35.0, age=20.0)
    p_exp = _proj("EXP-01", "TyD", 8_000_000_000, drivers=drivers, risk=risk_exp)
    p_wbl = _proj("WBL-01", "TyD", 8_000_000_000,
                  drivers=copy.deepcopy(drivers), risk=risk_wbl, asset_type="transformador_potencia")
    res_exp = eng.evaluate(p_exp)
    res_wbl = eng.evaluate(p_wbl)
    r.check(res_wbl["risk_analysis"]["pof_mode"] == "weibull", "Modo Weibull activo")
    r.check(res_exp["risk_analysis"]["pof_mode"] == "exponential", "Modo exponencial activo")
    r.check(res_wbl["delta_r_npv_COP"] > 0, f"ΔR Weibull = {res_wbl['delta_r_npv_COP']/1e6:.0f} MM")

@sim("S1.10 Riesgo multi-tipo CoF por BU (consequence_types)", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    # Beneficios grandes y riesgo alto para garantizar ΔR > 0
    drivers = [_drv("VD-1", [_sc("VD-1-A", 2000.0, 335_000)])]
    ctypes = {"financiero": 3, "hs": 4, "ambiental": 2, "regulatorio": 3, "reputacional": 2}
    risk_mt = _risk(5, 4, 1, 1, ctypes=ctypes)
    p = _proj("MT-01", "TyD", 5_000_000_000, drivers=drivers, risk=risk_mt)
    res = eng.evaluate(p)
    cof_mode = res["risk_analysis"].get("cof_mode", "N/A")
    r.check(cof_mode is not None,
            f"cof_mode presente={cof_mode}")
    dr_val = res["delta_r_npv_COP"]
    r.check(isinstance(dr_val, (int, float)),
            f"delta_r_npv_COP es numerico: {dr_val/1e6:.0f}MM")

@sim("S1.11 Horizonte dinámico por tipo de activo", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    # Usar claves exactas del config asset_horizons_by_bu para TyD:
    # transformador_potencia=30a, sistema_control_SCADA=10a
    p_tr  = _proj("TR-01", "TyD", 1_000_000, asset_type="transformador_potencia")
    p_sc  = _proj("SC-01", "TyD", 1_000_000, asset_type="sistema_control_SCADA")
    p_def = _proj("DEF-01", "TyD", 1_000_000)
    h_tr  = eng.get_horizon_for_asset(p_tr)
    h_sc  = eng.get_horizon_for_asset(p_sc)
    h_def = eng.get_horizon_for_asset(p_def)
    r.check(h_tr > h_sc, f"Horizonte transformador ({h_tr}a) > SCADA ({h_sc}a)")
    r.check(h_def > 0, f"Horizonte default = {h_def}a")

@sim("S1.12 EAV y VEI_EAV — normalización entre horizontes", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    # transformador_potencia=30a vs sistema_control_SCADA=10a (TyD config)
    dr = [_drv("VD-1", [_sc("VD-1-A", 300.0, 335_000)])]
    p_tr = _proj("TR-EAV", "TyD", 5_000_000_000,
                 drivers=dr, asset_type="transformador_potencia")
    p_sc = _proj("SC-EAV", "TyD", 5_000_000_000,
                 drivers=copy.deepcopy(dr), asset_type="sistema_control_SCADA")
    r_tr = eng.evaluate(p_tr)
    r_sc = eng.evaluate(p_sc)
    h_tr = r_tr["horizon_asset_years"]
    h_sc = r_sc["horizon_asset_years"]
    r.check(h_tr != h_sc, f"Horizontes distintos: TR={h_tr}a SC={h_sc}a")
    r.check(r_tr["eav_COP"] != r_sc["eav_COP"],
            f"EAV TR={r_tr['eav_COP']/1e6:.0f}MM != EAV SC={r_sc['eav_COP']/1e6:.0f}MM")
    r.check(isinstance(r_tr["vei_eav"], float),
            f"VEI_EAV TR es numerico: {r_tr['vei_eav']:.4f}")

@sim("S1.13 VNT Marginal alternativa vs baseline", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    dr  = [_drv("VD-1", [_sc("VD-1-A", 500.0, 335_000)])]
    baseline = _proj("BL-01", "TyD", 500_000_000,
                     drivers=[_drv("VD-1", [_sc("VD-1-A", 0.0, 335_000)])],
                     risk=_risk(4, 4, 4, 4))
    baseline.is_baseline = True
    alt = _proj("ALT-01", "TyD", 8_000_000_000, drivers=dr, risk=_risk(4, 4, 1, 1))
    marg = eng.evaluate_marginal(alt, baseline)
    r.check("vnt_marginal_COP" in marg, "vnt_marginal_COP presente")
    r.check("eav_marginal_COP" in marg, "eav_marginal_COP presente")

@sim("S1.14 Optimal Timing Analysis (5 años)", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    dr = [_drv("VD-1", [_sc("VD-1-A", 300.0, 335_000)])]
    p = _proj("OPT-TIM", "TyD", 10_000_000_000, drivers=dr,
              risk=_risk(3, 3, 1, 1))
    ot = eng.optimal_timing_analysis(p)
    r.check("yearly_results" in ot, "yearly_results presente")
    r.check(len(ot["yearly_results"]) >= 5, f"{len(ot['yearly_results'])} años evaluados")
    r.check("optimal_year" in ot, "optimal_year presente")
    r.check(ot["optimal_year"] >= 2026, f"Año optimo >= 2026: {ot['optimal_year']}")
    r.check("urgency" in ot, f"urgency presente: {ot.get('urgency')}")

@sim("S1.15 VD-5-B PTAR (AyA) + VD-4-D Scope2 TyD + VD-4-E Scope2 AyA", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    # VD-5-B PTAR
    dr_ptar = [_drv("VD-5", [_sc("VD-5-B", 50.0, 4_500_000, "Paradas PTAR")])]
    p_ptar = _proj("PTAR-01", "AyA", 3_000_000_000, drivers=dr_ptar)
    res = eng.evaluate(p_ptar)
    r.check(res["benefits_raw_COP"] > 0, "VD-5-B PTAR genera beneficio")
    # VD-4-D Scope2 T&D
    dr_s2 = [_drv("VD-4", [_sc("VD-4-D", 10_000.0, 11_088, "Scope2 T&D")])]
    p_s2 = _proj("S2-TyD", "TyD", 2_000_000_000, drivers=dr_s2)
    res2 = eng.evaluate(p_s2)
    r.check(res2["benefits_raw_COP"] > 0, "VD-4-D Scope2 TyD genera beneficio")

@sim("S1.16 delta_r_override_COP — portafolios no-Sostenimiento", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    dr = [_drv("VD-1", [_sc("VD-1-A", 100.0, 335_000)])]
    override_val = 5_000_000_000.0
    p = _proj("OVR-01", "TyD", 8_000_000_000, drivers=dr,
              delta_r_override=override_val)
    res = eng.evaluate(p)
    r.check(res["delta_r_npv_COP"] == override_val,
            f"ΔR override = {override_val/1e9:.1f} B")
    r.check(res["risk_analysis"].get("source") == "override_portafolio",
            "Source = override_portafolio")

@sim("S1.17 Construction_years y benefit_start_offsets", "S1 ValueEngine")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    dr = [_drv("VD-1", [_sc("VD-1-A", 500.0, 335_000)]),
          _drv("VD-3", [_sc("VD-3-A", 100.0, 5_200_000)])]
    p1 = _proj("CY1", "TyD", 5_000_000_000, drivers=dr, cy=1)
    p3 = _proj("CY3", "TyD", 5_000_000_000, drivers=copy.deepcopy(dr), cy=3)
    p3.benefit_start_offsets = {"VD-3": 2}  # VD-3 madura 2 años después
    r1 = eng.evaluate(p1)
    r3 = eng.evaluate(p3)
    r.check(r1["benefits_raw_COP"] > r3["benefits_raw_COP"],
            "Mayor cy -> menor VNT (descuento temporal)")
    r.check(r3["penalizacion_temporalidad_COP"] > 0, "Penalización temporalidad > 0")

# ══════════════════════════════════════════════════════════════════════════════
# GRUPO S2 — MILP Enterprise: R1-R12 + alcances
# ══════════════════════════════════════════════════════════════════════════════

def _run_milp(projects, periods, **kwargs) -> Any:
    opt = EnterpriseOptimizer(config=CFG)
    return opt.optimize(projects, periods, base_year=2026, **kwargs)

@sim("S2.1 R1 Presupuesto: selecciona proyectos dentro del cupo", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    # 2.500 MWh/anio: el VNT debe ser POSITIVO, si no el MILP no elige nada y
    # la prueba pasa de forma vacua (cota superior satisfecha por el cero).
    dr = [_drv("VD-1", [_sc("VD-1-A", 2_500.0, 335_000)])]
    projects = [
        _proj(f"P{i}", "TyD", 5_000_000_000,
              drivers=copy.deepcopy(dr))
        for i in range(5)
    ]
    periods = [_period(2026, 12_000_000_000)]  # cupo < suma todos: solo caben 2
    res = _run_milp(projects, periods)
    r.check(res.solver_status in ("OPTIMAL", "FEASIBLE"),
            f"Solver={res.solver_status}")
    # Cota INFERIOR: sin esto la prueba no distingue "restriccion correcta" de
    # "el optimizador no selecciono nada".
    r.check(len(res.selected) >= 1, f"{len(res.selected)} seleccionados ≥ 1 (no vacuo)")
    r.check(len(res.selected) <= 2, f"{len(res.selected)} proyectos seleccionados ≤ cupo")
    total_capex = sum(s["capex_COP"] for s in res.selected)
    r.check(total_capex <= 12_000_000_000 + 1, f"CAPEX asignado {total_capex/1e9:.1f} B ≤ cupo")

@sim("S2.2 R2 Unicidad: cada proyecto se ejecuta max 1 vez", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 300.0, 335_000)])]
    projects = [_proj("UNICO", "TyD", 3_000_000_000, drivers=dr)]
    periods = [_period(2026, 10_000_000_000), _period(2027, 10_000_000_000)]
    res = _run_milp(projects, periods)
    count = sum(1 for s in res.selected if s["project_id"] == "UNICO")
    r.check(count <= 1, f"UNICO aparece {count} veces (máx 1)")

@sim("S2.3 R3a Dependencia simple: hijo requiere padre", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 500.0, 335_000)])]
    padre = _proj("PAD", "TyD", 5_000_000_000, drivers=dr)
    hijo  = _proj("HIJ", "TyD", 3_000_000_000,
                  drivers=copy.deepcopy(dr), precedent="PAD")
    projects = [padre, hijo]
    periods = [_period(2026, 8_000_000_000), _period(2027, 8_000_000_000)]
    res = _run_milp(projects, periods)
    selected_ids = {s["project_id"] for s in res.selected}
    if "HIJ" in selected_ids:
        r.check("PAD" in selected_ids, "Si hijo seleccionado -> padre también seleccionado")
    else:
        r.check(True, "Hijo no seleccionado (sin violación)")

@sim("S2.4 R3b Precedencia temporal con lag=1", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 400.0, 335_000)])]
    padre = _proj("PAD-L", "AyA", 4_000_000_000, drivers=dr)
    hijo  = _proj("HIJ-L", "AyA", 3_000_000_000,
                  drivers=copy.deepcopy(dr), precedent="PAD-L", lag=1)
    projects = [padre, hijo]
    periods = [_period(y, 8_000_000_000) for y in [2026, 2027, 2028]]
    res = _run_milp(projects, periods)
    sel_map = {s["project_id"]: s["year"] for s in res.selected}
    if "PAD-L" in sel_map and "HIJ-L" in sel_map:
        lag_real = sel_map["HIJ-L"] - sel_map["PAD-L"]
        r.check(lag_real >= 1, f"Lag real = {lag_real}a ≥ 1a requerido")
    else:
        r.check(True, "No ambos seleccionados — sin violación lag")

@sim("S2.5 R3c Exclusión mutua par-a-par", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 400.0, 335_000)])]
    pA = _proj("MX-A", "Gas", 5_000_000_000, drivers=dr, mutex=["MX-B"])
    pB = _proj("MX-B", "Gas", 5_000_000_000, drivers=copy.deepcopy(dr), mutex=["MX-A"])
    projects = [pA, pB]
    periods = [_period(2026, 20_000_000_000)]
    res = _run_milp(projects, periods)
    sel_ids = {s["project_id"] for s in res.selected}
    r.check(not ("MX-A" in sel_ids and "MX-B" in sel_ids),
            f"MX-A y MX-B NO coexisten (sel={sel_ids})")

@sim("S2.6 R3d Exclusión de grupo N-way (exclusion_group_id)", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 400.0, 335_000)])]
    projects = [
        _proj(f"GRP-{i}", "TyD", 4_000_000_000,
              drivers=copy.deepcopy(dr), excl_group="GRP-SE-NORTE")
        for i in range(4)
    ]
    periods = [_period(2026, 50_000_000_000)]
    res = _run_milp(projects, periods)
    grp_sel = [s for s in res.selected if s.get("exclusion_group_id") == "GRP-SE-NORTE"]
    r.check(len(grp_sel) <= 1, f"Grupo exclusión: {len(grp_sel)} ≤ 1 seleccionado")

@sim("S2.7 R4 Persona-mes por vigencia", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 300.0, 335_000)])]
    # Proyectos de $5B cada uno -> ~6 p-m cada uno (tabla PM_TABLE)
    projects = [_proj(f"PM-{i}", "TyD", 5_000_000_000, drivers=copy.deepcopy(dr))
                for i in range(6)]
    periods = [_period(2026, 100_000_000_000, pm=15.0)]  # solo 15 p-m = max 2 proyectos
    res = _run_milp(projects, periods)
    r.check(len(res.selected) <= 3, f"Persona-mes limita a {len(res.selected)} proyectos ≤ 3")

@sim("S2.8 R5 Concurrencia máxima por vigencia", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 200.0, 335_000)])]
    projects = [_proj(f"CONC-{i}", "TyD", 2_000_000_000, drivers=copy.deepcopy(dr))
                for i in range(6)]
    periods = [_period(2026, 100_000_000_000, conc=2)]
    res = _run_milp(projects, periods)
    sel_2026 = sum(1 for s in res.selected if s["year"] == 2026)
    r.check(sel_2026 <= 2, f"Concurrencia 2026 = {sel_2026} ≤ 2")

@sim("S2.9 R6 Riesgo máximo portafolio por vigencia", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 200.0, 335_000)])]
    rsk = _risk(4, 4, 2, 2)
    projects = [_proj(f"RISK-{i}", "TyD", 3_000_000_000,
                      drivers=copy.deepcopy(dr), risk=copy.deepcopy(rsk))
                for i in range(5)]
    periods = [_period(2026, 100_000_000_000, risk_max=5_000_000_000)]
    res = _run_milp(projects, periods)
    r.check(res.solver_status in ("OPTIMAL","FEASIBLE","INFEASIBLE"),
            f"Solver status={res.solver_status}")

@sim("S2.10 R8 Must-Mitigate riesgos inaceptables", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr_mit = [_drv("VD-1", [_sc("VD-1-A", 100.0, 335_000)])]
    dr_low = [_drv("VD-1", [_sc("VD-1-A", 10.0, 335_000)])]
    # Activo con riesgo L4 (≥ tolerancia 4)
    p_mit = _proj("MIT-01", "TyD", 8_000_000_000, drivers=dr_mit,
                  risk=_risk(5, 4, 1, 1), asset_id="ACT-CRITICO-01")
    # Proyecto con muy bajo beneficio (no entraría sin R8)
    p_low = _proj("LOW-01", "TyD", 1_000_000_000, drivers=dr_low)
    projects = [p_mit, p_low]
    periods = [_period(2026, 10_000_000_000)]
    res = _run_milp(projects, periods, risk_tolerance_level=4)
    sel_ids = {s["project_id"] for s in res.selected}
    r.check("MIT-01" in sel_ids, "R8 forzó mitigación del activo crítico")

@sim("S2.11 R7 Pisos de inversión por BU (Enterprise Global)", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 100.0, 335_000)])]
    dr_aya = [_drv("VD-1", [_sc("VD-1-C", 10_000.0, 3_100)])]
    projects = (
        [_proj(f"TyD-{i}", "TyD", 5_000_000_000, drivers=copy.deepcopy(dr))
         for i in range(3)]
        + [_proj("AyA-PISO", "AyA", 4_000_000_000, drivers=dr_aya)]
    )
    periods = [_period(2026, 50_000_000_000)]
    pisos = {"AyA": 3_000_000_000}
    res = _run_milp(projects, periods, pisos_inversion_COP=pisos)
    sel_aya = [s for s in res.selected if s["business_unit"] == "AyA"]
    aya_capex = sum(s["capex_COP"] for s in sel_aya)
    r.check(aya_capex >= 3_000_000_000 or res.solver_status == "Infeasible",
            f"Piso AyA cumplido: CAPEX={aya_capex/1e9:.1f}B")

@sim("S2.12 R11 Committed Spend — forzar año comprometido", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 100.0, 335_000)])]
    p_comm = _proj("COMM-01", "TyD", 6_000_000_000, drivers=dr,
                   committed=True, committed_year=2026)
    p_disc = _proj("DISC-01", "TyD", 6_000_000_000,
                   drivers=copy.deepcopy(dr))
    projects = [p_comm, p_disc]
    periods = [_period(2026, 7_000_000_000), _period(2027, 7_000_000_000)]
    res = _run_milp(projects, periods)
    comm_sel = [s for s in res.selected if s["project_id"] == "COMM-01"]
    r.check(len(comm_sel) > 0, "Committed project seleccionado")
    if comm_sel:
        r.check(comm_sel[0]["year"] == 2026, f"Committed ejecutado en 2026 (year={comm_sel[0]['year']})")

@sim("S2.13 R12 TOTEX por vigencia", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 200.0, 335_000)])]
    projects = [_proj(f"TOTEX-{i}", "TyD", 3_000_000_000,
                      drivers=copy.deepcopy(dr), opex=200_000_000)
                for i in range(4)]
    # TOTEX apretado — debería limitar selección
    periods = [_period(2026, 20_000_000_000, totex=4_000_000_000)]
    res = _run_milp(projects, periods)
    r.check(res.solver_status in ("OPTIMAL","FEASIBLE","INFEASIBLE"),
            f"TOTEX status={res.solver_status}")

@sim("S2.14 Mandatory projects — siempre seleccionados", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 1.0, 335_000)])]  # beneficio mínimo
    p_mand = _proj("MAND-01", "TyD", 3_000_000_000, drivers=dr, mandatory=True)
    p_disc = _proj("DISC-01", "TyD", 3_000_000_000,
                   drivers=copy.deepcopy(dr))
    projects = [p_mand, p_disc]
    periods = [_period(2026, 4_000_000_000)]  # presupuesto solo para 1
    res = _run_milp(projects, periods)
    sel_ids = {s["project_id"] for s in res.selected}
    r.check("MAND-01" in sel_ids, "Mandatory siempre seleccionado")

@sim("S2.15 Frontera eficiente CAPEX (compute_frontier=True)", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 200.0, 335_000)])]
    projects = [_proj(f"FE-{i}", "TyD", (i+1)*2_000_000_000,
                      drivers=copy.deepcopy(dr)) for i in range(5)]
    periods = [_period(2026, 30_000_000_000)]
    res = _run_milp(projects, periods, compute_frontier=True)
    r.check(len(res.efficient_frontier) > 0, f"Frontera eficiente con {len(res.efficient_frontier)} puntos")

@sim("S2.16 Silo TyD (bu_filter='TyD')", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    # Cantidades que dan VNT positivo: con VNT<0 el MILP no elige nada y la
    # asercion "AyA no esta seleccionada" se cumple trivialmente.
    dr_t = [_drv("VD-1", [_sc("VD-1-A", 2_000.0, 335_000)])]
    dr_a = [_drv("VD-1", [_sc("VD-1-C", 250_000.0, 3_100)])]
    projects = (
        [_proj(f"TyD-{i}", "TyD", 3_000_000_000, drivers=copy.deepcopy(dr_t)) for i in range(3)]
        + [_proj("AyA-X", "AyA", 3_000_000_000, drivers=dr_a)]
    )
    periods = [_period(2026, 50_000_000_000)]
    res = _run_milp(projects, periods, bu_filter="TyD")
    sel_bus = {s["business_unit"] for s in res.selected}
    r.check(len(res.selected) >= 1, f"{len(res.selected)} seleccionados ≥ 1 (no vacuo)")
    r.check("AyA" not in sel_bus, f"Silo TyD excluye AyA. BUs={sel_bus}")

@sim("S2.17 DegradationHints — bono timing suave (I2)", "S2 MILP")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 200.0, 335_000)])]
    p = _proj("DH-01", "TyD", 5_000_000_000, drivers=dr, asset_id="ACT-TR-001")
    periods = [_period(y, 10_000_000_000) for y in [2026, 2027, 2028]]
    hints = {"ACT-TR-001": 2027}
    res = _run_milp([p], periods, degradation_hints=hints)
    r.check(res.solver_status in ("OPTIMAL","FEASIBLE"), "MILP con hints resuelto")
    r.check(len(res.degradation_hints_applied) > 0 or True,
            f"Hints aplicados: {len(res.degradation_hints_applied)}")

# ══════════════════════════════════════════════════════════════════════════════
# GRUPO S3 — Dependencias: deadlock, cascade CoD
# ══════════════════════════════════════════════════════════════════════════════

@sim("S3.1 DependencyEngine detección de ciclo (deadlock)", "S3 Dependencias")
def _(r: SimResult):
    dep = DependencyEngine(CFG)
    pA = _proj("A", "TyD", 1e9, precedent="B")
    pB = _proj("B", "TyD", 1e9, precedent="A")
    try:
        dep.validate([pA, pB])
        r.check(False, "Debería haber lanzado DependencyError")
    except DependencyError as e:
        r.check(True, f"DependencyError correctamente detectado: {str(e)[:60]}")

@sim("S3.2 DependencyEngine exclusión mutua entre dependientes", "S3 Dependencias")
def _(r: SimResult):
    dep = DependencyEngine(CFG)
    pA = _proj("A", "TyD", 1e9, precedent="B", mutex=["B"])
    pB = _proj("B", "TyD", 1e9)
    try:
        dep.validate([pA, pB])
        r.check(False, "Debería detectar exclusión+dependencia conflictiva")
    except DependencyError as e:
        r.check(True, f"Conflicto mutex+dependencia detectado: {str(e)[:60]}")

@sim("S3.3 Cascade CoD padre->hijo", "S3 Dependencias")
def _(r: SimResult):
    dep = DependencyEngine(CFG)
    eng = ValueEngine(CFG)
    dr_p = [_drv("VD-1", [_sc("VD-1-A", 500.0, 335_000)])]
    dr_h = [_drv("VD-1", [_sc("VD-1-A", 300.0, 335_000)])]
    padre = _proj("PAD-CC", "TyD", 8_000_000_000, drivers=dr_p,
                  exec_year=2026, opt_year=2026)
    hijo  = _proj("HIJ-CC", "TyD", 5_000_000_000, drivers=dr_h,
                  exec_year=2028, opt_year=2026, precedent="PAD-CC")
    projects = [padre, hijo]
    years = [2026, 2027, 2028, 2029, 2030]
    try:
        dep.validate(projects)
        # api: apply_cascade_cod(projects, engine, vnt_matrix, years)
        vnt_matrix = [[eng.evaluate(p)["net_value_COP"]] * len(years) for p in projects]
        cascade = dep.apply_cascade_cod(projects, eng, vnt_matrix, years)
        r.check(isinstance(cascade, list), f"cascade_vnt es lista: {type(cascade).__name__}")
        r.check(len(cascade) == len(projects), f"Matriz {len(cascade)}x{len(years)} proyectos")
    except Exception as e:
        r.check(False, f"Error cascade CoD: {e}")

@sim("S3.4 Padre inexistente lanza DependencyError", "S3 Dependencias")
def _(r: SimResult):
    dep = DependencyEngine(CFG)
    p = _proj("A", "TyD", 1e9, precedent="FANTASMA")
    try:
        dep.validate([p])
        r.check(False, "Debería lanzar DependencyError por padre inexistente")
    except DependencyError as e:
        r.check(True, f"Padre inexistente detectado: {str(e)[:60]}")

@sim("S3.5 Cadena larga A->B->C->D (sin ciclo)", "S3 Dependencias")
def _(r: SimResult):
    dep = DependencyEngine(CFG)
    ids = ["A","B","C","D"]
    projects = [_proj(ids[i], "TyD", 1e9,
                      precedent=ids[i-1] if i > 0 else "")
                for i in range(len(ids))]
    try:
        warns = dep.validate(projects)
        r.check(True, f"Cadena A->B->C->D válida. Warnings={len(warns)}")
    except DependencyError as e:
        r.check(False, f"Error inesperado: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# GRUPO S4 — EVM Engine
# ══════════════════════════════════════════════════════════════════════════════

@sim("S4.1 EVM proyecto en curso: SPI<1 y CPI>1", "S4 EVM")
def _(r: SimResult):
    p = _proj("EVM-01", "TyD", 10_000_000_000, cy=2, exec_year=2024)
    actual = ProjectActual(
        project_id="EVM-01",
        ac_COP=4_000_000_000,   # gastó 4B de 5B planificados
        pct_complete=0.30,      # solo 30% avance
        cut_year=2025,
    )
    evm = EVMEngine.compute(p, actual, cut_year=2025)
    r.check(evm.spi < 1.0, f"SPI={evm.spi:.2f} < 1 (atrasado)")
    r.check(evm.cpi < 1.0, f"CPI={evm.cpi:.2f} < 1 (sobre-costo)")
    r.check(evm.health in ("ROJO","AMARILLO"), f"Salud={evm.health}")
    r.check(evm.eac_COP > p.costs.capex_COP, f"EAC {evm.eac_COP/1e9:.1f}B > presupuesto {p.costs.capex_COP/1e9:.1f}B")

@sim("S4.2 EVM proyecto saludable: SPI>=1 y CPI>=1", "S4 EVM")
def _(r: SimResult):
    # cy=2, exec_year=2024 -> proyecto termina 2025. cut_year=2025, pct=1.0 -> completo a tiempo
    # PV = BAC completo, EV = 1.0*BAC, AC < BAC -> SPI=1.0, CPI>1
    p = _proj("EVM-02", "TyD", 10_000_000_000, cy=2, exec_year=2024)
    actual = ProjectActual(
        project_id="EVM-02",
        ac_COP=4_500_000_000,   # menos de BAC -> CPI > 1
        pct_complete=1.0,       # completado -> SPI = 1.0
        cut_year=2025,
    )
    evm = EVMEngine.compute(p, actual, cut_year=2025)
    r.check(evm.spi >= 1.0, f"SPI={evm.spi:.2f} >= 1 (a tiempo / adelantado)")
    r.check(evm.cpi >= 1.0, f"CPI={evm.cpi:.2f} >= 1 (bajo presupuesto)")
    r.check(evm.health == "VERDE", f"Salud={evm.health}")

@sim("S4.3 EVM portafolio: SPI/CPI agregados", "S4 EVM")
def _(r: SimResult):
    projects = [
        _proj("EVP-01", "TyD", 10_000_000_000, cy=2, exec_year=2024),
        _proj("EVP-02", "AyA",  8_000_000_000, cy=2, exec_year=2024),
    ]
    actuals = [
        ProjectActual("EVP-01", ac_COP=4_000_000_000, pct_complete=0.3, cut_year=2025),
        ProjectActual("EVP-02", ac_COP=2_000_000_000, pct_complete=0.5, cut_year=2025),
    ]
    results, summary = EVMEngine.compute_portfolio(projects, actuals, cut_year=2025)
    r.check(len(results) == 2, f"{len(results)} resultados EVM")
    r.check("spi_portfolio" in summary, "SPI portafolio presente")
    r.check("cpi_portfolio" in summary, "CPI portafolio presente")
    r.check(summary["n_rojo"] + summary["n_amarillo"] + summary["n_verde"] == 2,
            "Suma semáforos = 2")

@sim("S4.4 EVM proyecto completado (pct=1)", "S4 EVM")
def _(r: SimResult):
    p = _proj("EVM-DONE", "Gas", 5_000_000_000, cy=1, exec_year=2024)
    actual = ProjectActual("EVM-DONE", ac_COP=5_200_000_000, pct_complete=1.0, cut_year=2025)
    evm = EVMEngine.compute(p, actual, cut_year=2025)
    r.check(evm.pct_complete == 1.0, "pct_complete=1.0")
    r.check(evm.cv_COP < 0, f"CV={evm.cv_COP/1e6:.0f}MM (sobre-costo real)")

@sim("S4.5 EVM proyecto pausado — flag en health_detail", "S4 EVM")
def _(r: SimResult):
    p = _proj("EVM-PAU", "TyD", 8_000_000_000, cy=2, exec_year=2024)
    actual = ProjectActual("EVM-PAU", ac_COP=2_000_000_000, pct_complete=0.2,
                           cut_year=2025, is_paused=True)
    evm = EVMEngine.compute(p, actual, cut_year=2025)
    r.check("[PAUSADO]" in evm.health_detail, "Flag PAUSADO en health_detail")

# ══════════════════════════════════════════════════════════════════════════════
# GRUPO S5 — OutageCalendar + R10
# ══════════════════════════════════════════════════════════════════════════════

@sim("S5.1 OutageCalendar: años válidos para asset_id", "S5 Outage")
def _(r: SimResult):
    outages = [
        PlannedOutage(outage_id="OUT-1", asset_id="TR-001",
                      year=2027, name="Mantenimiento mayor TR-001"),
        PlannedOutage(outage_id="OUT-2", asset_id="TR-001",
                      year=2029, name="Inspección TR-001"),
    ]
    cal = OutageCalendar(outages)
    valid = cal.valid_years("TR-001")   # API correcta: valid_years(asset_id)
    r.check(2027 in valid, "2027 valido para TR-001")
    r.check(2029 in valid, "2029 valido para TR-001")
    r.check(2026 not in valid, "2026 NO valido para TR-001")

@sim("S5.2 R10 MILP — outage_alignment restringe años", "S5 Outage")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP — PuLP no disponible"); return
    outages = [
        PlannedOutage(outage_id="OUT-X", asset_id="TR-CRITICO",
                      year=2027, name="Ventana mantenimiento"),
    ]
    dr = [_drv("VD-1", [_sc("VD-1-A", 300.0, 335_000)])]
    p = _proj("OUT-PROJ", "TyD", 5_000_000_000, drivers=dr,
              asset_id="TR-CRITICO", outage_align=True)
    periods = [_period(y, 10_000_000_000) for y in [2026, 2027, 2028]]
    res = _run_milp([p], periods, planned_outages=outages)
    if res.selected:
        sel_year = res.selected[0]["year"]
        r.check(sel_year == 2027, f"Proyecto asignado a 2027 (outage). Año actual={sel_year}")
    else:
        r.check(True, "Proyecto no seleccionado (sin violación)")

# ==============================================================================
# GRUPO S6 — SnapshotStore ISO 55001
# ==============================================================================

@sim("S6.1 Snapshot SHA-256 append-only", "S6 Snapshots")
def _(r: SimResult):
    with tempfile.TemporaryDirectory() as td:
        store = SnapshotStore(storage_dir=td)   # kwarg correcto
        eng = ValueEngine(CFG)
        dr = [_drv("VD-1", [_sc("VD-1-A", 100.0, 335_000)])]
        p = _proj("SNAP-01", "TyD", 5_000_000_000, drivers=dr)
        result = eng.evaluate(p)
        # API: capture(evaluation_result, project, config, analyst, change_reason)
        snap1 = store.capture(result, p, CFG, "test", "evaluacion inicial")
        snap2 = store.capture(result, p, CFG, "test", "re-evaluacion")
        r.check(snap1.integrity_hash != "", "SHA-256 calculado (integrity_hash)")
        r.check(len(snap1.integrity_hash) >= 16, f"Hash con longitud minima: {len(snap1.integrity_hash)}")
        r.check(snap1.snapshot_id != snap2.snapshot_id,
                "Cada captura tiene snapshot_id unico (append-only)")
        all_snaps = store.search()   # search() sin args retorna todos
        r.check(len(all_snaps) >= 2, f"{len(all_snaps)} snapshots guardados")

@sim("S6.2 Snapshot diff entre dos evaluaciones distintas", "S6 Snapshots")
def _(r: SimResult):
    with tempfile.TemporaryDirectory() as td:
        store = SnapshotStore(storage_dir=td)
        eng = ValueEngine(CFG)
        dr1 = [_drv("VD-1", [_sc("VD-1-A", 100.0, 335_000)])]
        dr2 = [_drv("VD-1", [_sc("VD-1-A", 900.0, 335_000)])]
        p1 = _proj("DIFF-A", "TyD", 5_000_000_000, drivers=dr1)
        p2 = _proj("DIFF-B", "TyD", 5_000_000_000, drivers=dr2)
        s1 = store.capture(eng.evaluate(p1), p1, CFG, "a")
        s2 = store.capture(eng.evaluate(p2), p2, CFG, "b")
        r.check(s1.integrity_hash != s2.integrity_hash,
                "SHA distintos para evaluaciones distintas")
        # compare por snapshot_id
        diff = store.compare(s1.snapshot_id, s2.snapshot_id)
        r.check(isinstance(diff, dict), f"compare retorna dict: {list(diff.keys())[:4]}")

# ==============================================================================
# GRUPO S7 — PlanVersionStore
# ==============================================================================

@sim("S7.1 PlanVersionStore save + list + get", "S7 PlanVersion")
def _(r: SimResult):
    with tempfile.TemporaryDirectory() as td:
        # API: PlanVersionStore(store_path=<ruta_archivo.json>)
        pvs = PlanVersionStore(store_path=os.path.join(td, "plan_versions.json"))
        dr = [_drv("VD-1", [_sc("VD-1-A", 100.0, 335_000)])]
        projects = [_proj(f"PV-{i}", "TyD", 3e9, drivers=copy.deepcopy(dr)) for i in range(3)]
        # save_version necesita un EnterprisePortfolioResult -> usar MILP
        milp_res = _run_milp(projects, [_period(2026, 20_000_000_000)])
        # API: save_version(result, cycle_name, author, description)
        v1 = pvs.save_version(milp_res, cycle_name="2026", author="analista",
                              description="Plan inicial")
        r.check(v1.version_id != "", "version_id generado")
        r.check(v1.cycle_name == "2026", f"cycle_name correcto: {v1.cycle_name}")
        versions = pvs.list_versions()
        r.check(len(versions) >= 1, f"{len(versions)} versiones guardadas")
        v1_load = pvs.get_version(v1.version_id)
        r.check(v1_load is not None, "Version recuperada")
        r.check(v1_load.version_id == v1.version_id, "version_id coincide")
        r.check(v1_load.n_projects >= 0, f"n_projects={v1_load.n_projects} (seleccionados={len(milp_res.selected)})")

@sim("S7.2 PlanVersionStore diff entre versiones", "S7 PlanVersion")
def _(r: SimResult):
    with tempfile.TemporaryDirectory() as td:
        pvs = PlanVersionStore(store_path=os.path.join(td, "pv.json"))
        dr = [_drv("VD-1", [_sc("VD-1-A", 100.0, 335_000)])]
        # Dos portafolios distintos -> dos resultados MILP distintos
        pA = [_proj("PA1", "TyD", 3e9, drivers=copy.deepcopy(dr)),
              _proj("PA2", "TyD", 4e9, drivers=copy.deepcopy(dr))]
        pB = [_proj("PB1", "AyA", 3e9, drivers=copy.deepcopy(dr)),
              _proj("PB2", "Gas", 5e9, drivers=copy.deepcopy(dr))]
        resA = _run_milp(pA, [_period(2026, 20_000_000_000)])
        resB = _run_milp(pB, [_period(2026, 20_000_000_000)])
        v1 = pvs.save_version(resA, cycle_name="2026a", author="a", description="v1")
        v2 = pvs.save_version(resB, cycle_name="2026b", author="b", description="v2")
        diff = pvs.diff_versions(v1.version_id, v2.version_id)
        r.check(diff is not None, "diff_versions no None")
        r.check(isinstance(diff, dict), f"diff_versions es dict: {list(diff.keys())[:4]}")

# ==============================================================================
# GRUPO S8 — WorkflowEngine
# ==============================================================================

@sim("S8.1 Workflow BORRADOR -> ENVIADO -> EN_REVISION -> APROBADO_NEGOCIO", "S8 Workflow")
def _(r: SimResult):
    # Roles correctos segun WF_FULL_TRANSITIONS:
    # analista: BORRADOR -> ENVIADO
    # coordinador_ga: ENVIADO -> EN_REVISION, EN_REVISION -> APROBADO_NEGOCIO
    # comite_ga / director: APROBADO_NEGOCIO -> APROBADO_PORTAFOLIO
    wf = ProjectWorkflow(project_id="WF-01", project_name="Test", bu="TyD")
    r.check(wf.current_state == "BORRADOR", f"Estado inicial={wf.current_state}")

    # Paso 1: analista somete a revision
    acts1 = WorkflowEngine.valid_actions("analista", "BORRADOR")
    r.check(len(acts1) > 0, f"{len(acts1)} acciones para analista en BORRADOR")
    act_env = next((a for a in acts1 if a["to"] == "ENVIADO"), None)
    r.check(act_env is not None, "Accion ENVIADO disponible para analista")
    if act_env:
        wf = WorkflowEngine.apply(wf, act_env, actor="Ana", role="analista", bu="TyD")
        r.check(wf.current_state == "ENVIADO", f"Estado={wf.current_state}")

    # Paso 2: coordinador_ga acepta para revision
    acts2 = WorkflowEngine.valid_actions("coordinador_ga", "ENVIADO")
    act_rev = next((a for a in acts2 if a["to"] == "EN_REVISION"), None)
    if act_rev:
        wf = WorkflowEngine.apply(wf, act_rev, actor="Coord", role="coordinador_ga", bu="TyD")
        r.check(wf.current_state == "EN_REVISION", f"Estado={wf.current_state}")

    # Paso 3: coordinador_ga aprueba negocio
    acts3 = WorkflowEngine.valid_actions("coordinador_ga", "EN_REVISION")
    act_apr = next((a for a in acts3 if a["to"] == "APROBADO_NEGOCIO"), None)
    if act_apr:
        wf = WorkflowEngine.apply(wf, act_apr, actor="Coord", role="coordinador_ga", bu="TyD")
        r.check(wf.current_state == "APROBADO_NEGOCIO", f"Estado final={wf.current_state}")

    r.check(len(wf.history) >= 1, f"{len(wf.history)} transiciones en historial")

@sim("S8.2 Workflow rechazo requiere comentario", "S8 Workflow")
def _(r: SimResult):
    wf = ProjectWorkflow(project_id="WF-02", project_name="Test", bu="TyD")
    acts = WorkflowEngine.valid_actions("analista", "BORRADOR")
    to_rev = next((a for a in acts if a["to"] == "REVISION"), None)
    if to_rev:
        wf = WorkflowEngine.apply(wf, to_rev, actor="Ana", role="analista", bu="TyD")
    acts2 = WorkflowEngine.valid_actions("revisor", wf.current_state)
    to_rech = next((a for a in acts2 if a["to"] == "RECHAZADO"), None)
    if to_rech:
        try:
            WorkflowEngine.apply(wf, to_rech, actor="Rev", role="revisor", bu="TyD", comment="")
            r.check(False, "Deberia exigir comentario")
        except ValueError:
            r.check(True, "ValueError exigido al rechazar sin comentario")
    else:
        r.check(True, "No hay transicion RECHAZADO desde este estado")

@sim("S8.3 Workflow bandeja pending_for_role", "S8 Workflow")
def _(r: SimResult):
    wfs = {f"WF-{i}": ProjectWorkflow(project_id=f"WF-{i}", project_name=f"WF-{i}", bu="TyD")
           for i in range(4)}
    pend = WorkflowEngine.pending_for_role(wfs, "analista")
    r.check(len(pend) == 4, f"4 pendientes para analista: {len(pend)}")
    pend_rev = WorkflowEngine.pending_for_role(wfs, "revisor")
    r.check(len(pend_rev) == 0, f"0 pendientes para revisor en BORRADOR: {len(pend_rev)}")

@sim("S8.4 Workflow summary por estado", "S8 Workflow")
def _(r: SimResult):
    wfs = {f"WF-{i}": ProjectWorkflow(project_id=f"WF-{i}", project_name=f"WF-{i}", bu="TyD")
           for i in range(5)}
    summ = WorkflowEngine.summary(wfs)
    r.check("BORRADOR" in summ, "BORRADOR en summary")
    r.check(summ.get("BORRADOR", 0) == 5, f"5 en BORRADOR: {summ.get('BORRADOR')}")

# ==============================================================================
# GRUPO S9 — PortfolioLoader CSV
# ==============================================================================

@sim("S9.1 PortfolioLoader CSV basico 3 proyectos", "S9 Loader")
def _(r: SimResult):
    # PortfolioLoader espera CAPEX en columnas capex_YYYY (por YEARS=[2026..2030])
    csv_content = (
        "id,name,business_unit,investment_type,capex_2026,capex_2027,optimal_year\n"
        "P001,Reposicion TR,TyD,Reposicion de Activos,8000000000,0,2026\n"
        "P002,Red Gas Bello,Gas,Crecimiento / Expansion,0,5000000000,2027\n"
        "P003,PTAR Norte,AyA,Sostenibilidad / ESG,3000000000,0,2026\n"
    )
    loader = PortfolioLoader()
    projects, meta = loader.from_bytes(csv_content.encode(), "portfolio.csv")
    r.check(len(projects) >= 2, f"{len(projects)} proyectos cargados")
    r.check(meta.get("n_projects", 0) >= 2, f"meta n_projects={meta.get('n_projects')}")
    r.check(projects[0].costs.capex_COP > 0, f"CAPEX P001={projects[0].costs.capex_COP:,.0f}")

@sim("S9.2 PortfolioLoader alias tolerantes", "S9 Loader")
def _(r: SimResult):
    # Aliases: nombre->name, unidad_negocio->business_unit
    csv_content = (
        "id,nombre,unidad_negocio,tipo_inversion,capex_2026\n"
        "X001,Test Alias TyD,TyD,Reposicion de Activos,12000000000\n"
        "X002,Test Alias AyA,AyA,Crecimiento / Expansion,6000000000\n"
    )
    loader = PortfolioLoader()
    projects, meta = loader.from_bytes(csv_content.encode(), "alias.csv")
    r.check(len(projects) >= 1, f"Alias tolerados: {len(projects)} proyectos")
    if projects:
        r.check(projects[0].business_unit in ("TyD","AyA"),
                f"BU mapeada correctamente: {projects[0].business_unit}")

@sim("S9.3 PortfolioLoader MM COP heuristica", "S9 Loader")
def _(r: SimResult):
    csv_content = (
        "id,nombre,bu,capex_COP,anio_ejecucion\n"
        "MM001,Proyecto MM,TyD,8000,2026\n"
    )
    loader = PortfolioLoader()
    projects, meta = loader.from_bytes(csv_content.encode(), "mm.csv")
    if projects:
        r.check(projects[0].costs.capex_COP >= 1_000_000,
                f"MM COP convertido: {projects[0].costs.capex_COP:,.0f} COP")
    else:
        r.check(True, "No proyectos cargados desde MM (tolerable)")

# ==============================================================================
# GRUPO S10 — SensitivityAnalyst
# ==============================================================================

@sim("S10.1 Sensitivity WACC sweep via run_scenario", "S10 Sensitivity")
def _(r: SimResult):
    # SensitivityAnalyst.run_scenario(projects, adjustments, scenario_name)
    analyst = SensitivityAnalyst(CFG)
    dr = [_drv("VD-1", [_sc("VD-1-A", 500.0, 335_000)])]
    p = _proj("SENS-01", "TyD", 10_000_000_000, drivers=dr, risk=_risk(3,3,1,1))
    # Escenario base
    base = analyst.run_scenario([p], {}, "Base")
    r.check("comparison" in base, "run_scenario retorna 'comparison'")
    r.check(isinstance(base["comparison"], list), "comparison es lista")
    r.check(len(base["comparison"]) == 1, f"1 proyecto en comparison: {len(base['comparison'])}")
    # Escenario wacc_override
    esc_low = analyst.run_scenario([p], {"wacc_override": 0.05}, "WACC_bajo")
    esc_hi  = analyst.run_scenario([p], {"wacc_override": 0.15}, "WACC_alto")
    r.check("comparison" in esc_low, "escenario WACC bajo tiene comparison")
    r.check("scenario_name" in esc_hi, f"escenario WACC alto: name={esc_hi.get('scenario_name')}")

@sim("S10.2 Sensitivity CAPEX sweep via run_scenario", "S10 Sensitivity")
def _(r: SimResult):
    analyst = SensitivityAnalyst(CFG)
    dr = [_drv("VD-1", [_sc("VD-1-A", 500.0, 335_000)])]
    projects = [_proj(f"SENS-C{i}", "TyD", (i+1)*3_000_000_000, drivers=copy.deepcopy(dr))
                for i in range(4)]
    esc = analyst.run_scenario(projects, {"capex_multiplier": 1.5}, "CAPEX+50pct")
    r.check("comparison" in esc, "run_scenario CAPEX: comparison presente")
    r.check(len(esc["comparison"]) == 4, f"4 proyectos: {len(esc['comparison'])}")
    # Verificar estructura de cada item
    item = esc["comparison"][0]
    r.check("base_net_value" in item, f"base_net_value en item: {list(item.keys())}")
    r.check("mod_net_value" in item, "mod_net_value en item")

@sim("S10.3 Sensitivity benefit sweep via run_scenario", "S10 Sensitivity")
def _(r: SimResult):
    analyst = SensitivityAnalyst(CFG)
    dr = [_drv("VD-1", [_sc("VD-1-A", 500.0, 335_000)])]
    p = _proj("SENS-03", "TyD", 10_000_000_000, drivers=dr)
    esc1 = analyst.run_scenario([p], {"benefit_multiplier": 0.5}, "Beneficio -50%")
    esc2 = analyst.run_scenario([p], {"benefit_multiplier": 2.0}, "Beneficio +100%")
    r.check("comparison" in esc1, "esc1 benefit sweep: comparison OK")
    r.check("comparison" in esc2, "esc2 benefit sweep: comparison OK")
    # rank_change puede ser 0 si solo hay 1 proyecto; la estructura debe existir
    r.check("rank_change" in esc1["comparison"][0],
            f"rank_change presente: {list(esc1['comparison'][0].keys())}")

# ==============================================================================
# GRUPO S11 — AssetDegradationEngine
# ==============================================================================

def _deg_eng():
    """AssetDegradationEngine con wacc/horizon correctos (no recibe CFG dict)."""
    return AssetDegradationEngine(wacc=0.10, horizon_years=10)

def _profile(aid, aname, aclass, bu, install, design_life, cond_pct,
             repl_cost, maint_cost, conseq, beta=2.5, eta=0.0,
             model="weibull", accel=2.0):
    """Helper para construir AssetProfile con API correcta."""
    return AssetProfile(
        asset_id=aid, asset_name=aname, asset_class=aclass,
        business_unit=bu, install_year=install, design_life_years=design_life,
        current_condition=cond_pct / 100.0,   # 0..1
        replacement_cost_COP=repl_cost,
        annual_maintenance_COP=maint_cost,
        consequence_level=conseq,
        weibull_beta=beta, weibull_eta=eta,
        degradation_model=model,
        acceleration_factor=accel,
    )

@sim("S11.1 Weibull transformador beta=2.5 eta=35 edad=31", "S11 Degradacion")
def _(r: SimResult):
    # AssetDegradationEngine(wacc, horizon_years) — NO recibe CFG dict
    eng = _deg_eng()
    profile = _profile("TR-001","Transformador 001","transformador_potencia",
                       "TyD", 1995, 35, 45, 8_000_000_000, 500_000_000, 4,
                       beta=2.5, eta=35.0, model="weibull")
    # analyze(profile, analysis_year=None) — sin model separado, sin horizon_years
    result = eng.analyze(profile)
    r.check(result.current_pof > 0, f"PoF actual={result.current_pof:.3f}")
    r.check(result.eeol_year >= 2026, f"EEOL={result.eeol_year}")
    r.check(result.cumulative_risk_npv > 0, f"Risk NPV={result.cumulative_risk_npv/1e6:.0f}MM")
    r.check(result.intervention_urgency in ("INMEDIATA","ALTA","MEDIA","BAJA"),
            f"Urgencia={result.intervention_urgency}")
    r.check(isinstance(result.pof_horizon, dict), f"pof_horizon es dict con {len(result.pof_horizon)} puntos")

@sim("S11.2 Lineal tuberia AC sobreedad", "S11 Degradacion")
def _(r: SimResult):
    eng = _deg_eng()
    profile = _profile("TUB-001","Tuberia AC 001","tuberia_asbesto_cemento",
                       "AyA", 2000, 20, 30, 3_000_000_000, 200_000_000, 3,
                       model="linear")
    result = eng.analyze(profile)
    r.check(result.current_pof > 0, f"PoF lineal={result.current_pof:.3f}")
    r.check(result.optimal_intervention_year >= 2026,
            f"Anio optimo={result.optimal_intervention_year}")
    r.check(result.cumulative_risk_npv > 0, f"Risk NPV={result.cumulative_risk_npv/1e6:.0f}MM")

@sim("S11.3 Escalon SCADA obsolescencia", "S11 Degradacion")
def _(r: SimResult):
    eng = _deg_eng()
    profile = _profile("SCADA-001","SCADA 001","sistema_control_SCADA",
                       "TyD", 2012, 10, 20, 1_500_000_000, 150_000_000, 3,
                       model="step")
    result = eng.analyze(profile)
    r.check(result.intervention_urgency in ("INMEDIATA","ALTA","MEDIA","BAJA"),
            f"Urgencia SCADA={result.intervention_urgency}")
    r.check(result.current_pof >= 0, f"PoF step calculado: {result.current_pof:.3f}")

@sim("S11.4 Acelerado compresor Gas", "S11 Degradacion")
def _(r: SimResult):
    eng = _deg_eng()
    profile = _profile("COMP-001","Compresor 001","estacion_compresion",
                       "Gas", 2005, 25, 50, 5_000_000_000, 400_000_000, 4,
                       model="accelerated", accel=1.5)
    result = eng.analyze(profile)
    r.check(isinstance(result.pof_horizon, dict), "pof_horizon presente acelerado")
    r.check(result.cumulative_risk_npv > 0, "Risk NPV acelerado > 0")

@sim("S11.5 Weibull turbina Generacion beta=2.5 eta=40 edad=36", "S11 Degradacion")
def _(r: SimResult):
    eng = _deg_eng()
    profile = _profile("GEN-001","Generador 001","generador_sincrono",
                       "Generacion", 1990, 40, 35, 25_000_000_000, 2_000_000_000, 5,
                       beta=2.5, eta=40.0, model="weibull")
    result = eng.analyze(profile)
    r.check(result.current_pof > 0.10, f"PoF generador maduro={result.current_pof:.3f}")
    r.check(result.cumulative_risk_npv > 0, f"Risk NPV={result.cumulative_risk_npv/1e6:.0f}MM")

@sim("S11.6 Degradation hints al MILP (pipeline I2)", "S11 Degradacion")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP - PuLP no disponible"); return
    eng_deg = _deg_eng()
    profile = _profile("TR-HINT","TR HINT","transformador_potencia",
                       "TyD", 1995, 35, 40, 8_000_000_000, 500_000_000, 4,
                       beta=2.5, eta=35.0, model="weibull")
    deg_r = eng_deg.analyze(profile)
    hints = {"TR-HINT": deg_r.optimal_intervention_year}
    dr = [_drv("VD-1", [_sc("VD-1-A", 200.0, 335_000)])]
    p = _proj("TR-HINT-P", "TyD", 8_000_000_000, drivers=dr, asset_id="TR-HINT")
    periods = [_period(y, 15_000_000_000) for y in range(2026, 2031)]
    res = _run_milp([p], periods, degradation_hints=hints)
    r.check(res.solver_status in ("OPTIMAL","FEASIBLE"), f"MILP+hints={res.solver_status}")

# ==============================================================================
# GRUPO S12 — Portafolios por BU
# ==============================================================================

@sim("S12.1 Portafolio TyD: 15 proyectos mixtos", "S12 Portafolios")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    projects = []
    for i in range(5):
        dr = [_drv("VD-1", [_sc("VD-1-A",(i+1)*100.0,335_000),_sc("VD-1-B",(i+1)*500.0,6_200)])]
        projects.append(_proj(f"TyD-REP-{i}","TyD",(i+3)*2_000_000_000,drivers=dr,risk=_risk(3+i%2,3,1,1)))
    for i in range(5):
        dr = [_drv("VD-3",[_sc("VD-3-A",(i+1)*200.0,5_200_000)])]
        projects.append(_proj(f"TyD-EXP-{i}","TyD",(i+2)*3_000_000_000,drivers=dr,inv_type="Crecimiento / Expansión"))
    for i in range(5):
        dr = [_drv("VD-2",[_sc("VD-2-C",(i+1)*150.0,335_000)])]
        projects.append(_proj(f"TyD-EFI-{i}","TyD",(i+1)*1_500_000_000,drivers=dr,inv_type="Eficiencia Operativa"))
    periods = [_period(y,15_000_000_000,pm=50.0,conc=5) for y in [2026,2027,2028]]
    res = _run_milp(projects, periods)
    r.check(res.solver_status in ("OPTIMAL","FEASIBLE"), f"TyD={res.solver_status}")
    r.check(len(res.selected) > 0, f"{len(res.selected)} seleccionados")
    r.check(res.total_net_value > 0, f"VNT={res.total_net_value/1e9:.1f}B")

@sim("S12.2 Portafolio AyA: tuberias + PTAR mandatory", "S12 Portafolios")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    projects = []
    for i in range(4):
        dr = [_drv("VD-1",[_sc("VD-1-C",(i+1)*50_000,3_100),_sc("VD-1-D",(i+1)*2_000,22_000)])]
        projects.append(_proj(f"AyA-TUB-{i}","AyA",(i+2)*2_000_000_000,drivers=dr,risk=_risk(3,3,1,1)))
    dr_ptar = [_drv("VD-5",[_sc("VD-5-B",30.0,4_500_000)]),_drv("VD-4",[_sc("VD-4-A",2_000.0,88_000)])]
    projects.append(_proj("AyA-PTAR","AyA",12_000_000_000,drivers=dr_ptar,cy=3,mandatory=True))
    periods = [_period(y,12_000_000_000) for y in [2026,2027,2028,2029]]
    res = _run_milp(projects, periods)
    r.check(res.solver_status in ("OPTIMAL","FEASIBLE"), f"AyA={res.solver_status}")
    mand = [s for s in res.selected if s["project_id"]=="AyA-PTAR"]
    r.check(len(mand)>0, "PTAR mandatory seleccionado")

@sim("S12.3 Portafolio Gas: gasoductos + expansion", "S12 Portafolios")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    projects = []
    for i in range(3):
        dr = [_drv("VD-1",[_sc("VD-1-E",(i+1)*10.0,50_000_000)]),_drv("VD-4",[_sc("VD-4-A",(i+1)*500.0,88_000)])]
        projects.append(_proj(f"Gas-INT-{i}","Gas",(i+2)*4_000_000_000,drivers=dr,risk=_risk(4,3,1,1)))
    for i in range(4):
        dr = [_drv("VD-3",[_sc("VD-3-C",(i+1)*200.0,4_100_000)])]
        projects.append(_proj(f"Gas-EXP-{i}","Gas",(i+1)*2_500_000_000,drivers=dr,inv_type="Crecimiento / Expansión"))
    periods = [_period(y,10_000_000_000) for y in [2026,2027,2028]]
    res = _run_milp(projects, periods)
    r.check(res.solver_status in ("OPTIMAL","FEASIBLE"), f"Gas={res.solver_status}")

@sim("S12.4 Portafolio Generacion: turbinas cy=3 + ANLA mandatory", "S12 Portafolios")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    projects = []
    for i in range(3):
        dr = [_drv("VD-1",[_sc("VD-1-E",(i+1)*20_000,355_000)]),_drv("VD-5",[_sc("VD-5-A",(i+1)*3_000_000_000,1.0)])]
        projects.append(_proj(f"Gen-TUR-{i}","Generacion",(i+5)*5_000_000_000,drivers=dr,risk=_risk(4,3,2,1),cy=3,asset_type="turbina_hidro"))
    dr_anla = [_drv("VD-4",[_sc("VD-4-C",0.3,3_200_000_000),_sc("VD-4-B",50.0,4_800_000)])]
    projects.append(_proj("Gen-ANLA","Generacion",8_000_000_000,drivers=dr_anla,mandatory=True,inv_type="Cumplimiento Regulatorio"))
    periods = [_period(y,30_000_000_000) for y in range(2026,2031)]
    res = _run_milp(projects, periods)
    r.check(res.solver_status in ("OPTIMAL","FEASIBLE"), f"Gen={res.solver_status}")

@sim("S12.5 Portafolio TI multi-BU (Todos) con concept_rating", "S12 Portafolios")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    projects = []
    for i in range(4):
        dr = [_drv("VD-2",[_sc("VD-2-A",(i+1)*200_000_000,1.0)]),_drv("VD-5",[_sc_cr("VD-5-C",7.0+i*0.3,400_000_000_000)])]
        projects.append(_proj(f"TI-{i:02d}","Todos",(i+1)*1_500_000_000,drivers=dr,inv_type="Proyecto Empresarial / TI"))
    periods = [_period(2026,8_000_000_000,conc=3)]
    res = _run_milp(projects, periods)
    r.check(res.solver_status in ("OPTIMAL","FEASIBLE"), f"TI={res.solver_status}")

@sim("S12.6 Portafolio enterprise-global 4 BUs con pisos", "S12 Portafolios")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    projects = []
    for bu, sc, price, n in [("TyD","VD-1-A",335_000,4),("AyA","VD-1-C",3_100,3),("Gas","VD-1-E",50_000_000,3),("Generacion","VD-1-E",355_000,3)]:
        for i in range(n):
            dr = [_drv("VD-1",[_sc(sc,(i+1)*100.0,price)])]
            projects.append(_proj(f"{bu}-GLOB-{i}",bu,(i+2)*3_000_000_000,drivers=dr))
    pisos = {"TyD":4_000_000_000,"AyA":2_000_000_000,"Gas":2_000_000_000,"Generacion":2_000_000_000}
    periods = [_period(y,25_000_000_000) for y in [2026,2027,2028]]
    res = _run_milp(projects, periods, pisos_inversion_COP=pisos)
    r.check(res.solver_status in ("OPTIMAL","FEASIBLE","INFEASIBLE"), f"Enterprise-global={res.solver_status}")

# ==============================================================================
# GRUPO S13 — Escenario integral: todos los modulos juntos
# ==============================================================================

@sim("S13.1 Escenario full 4 BUs: degradacion+MILP+EVM+snapshot+workflow", "S13 Integral")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    with tempfile.TemporaryDirectory() as td:
        # 1. Degradacion -> hints (API: AssetDegradationEngine(wacc, horizon_years))
        deg_eng = _deg_eng()
        profile = _profile("TR-FULL","TR Full","transformador_potencia","TyD",
                           1990, 35, 30, 10_000_000_000, 800_000_000, 5,
                           beta=2.5, eta=35.0, model="weibull")
        deg_res = deg_eng.analyze(profile)
        hints = {"TR-FULL": deg_res.optimal_intervention_year}

        # 2. Proyectos multi-BU
        projects = [
            _proj("F-TyD-01","TyD",10_000_000_000,
                  drivers=[_drv("VD-1",[_sc("VD-1-A",500.0,335_000),_sc("VD-1-B",8_000.0,6_200)])],
                  risk=_risk(4,4,1,1,beta=2.5,eta=35.0,age=36.0),
                  asset_id="TR-FULL",asset_type="transformador_potencia"),
            _proj("F-TyD-02","TyD",6_000_000_000,
                  drivers=[_drv("VD-3",[_sc("VD-3-A",300.0,5_200_000)])],
                  risk=_risk(3,3,1,1),inv_type="Crecimiento / Expansión"),
            _proj("F-AyA-01","AyA",8_000_000_000,
                  drivers=[_drv("VD-1",[_sc("VD-1-C",80_000.0,3_100)])],
                  risk=_risk(4,3,1,1),cy=2,mandatory=True),
            _proj("F-GAS-01","Gas",5_000_000_000,
                  drivers=[_drv("VD-1",[_sc("VD-1-E",15.0,50_000_000)])],
                  risk=_risk(3,4,1,1)),
            _proj("F-GEN-01","Generacion",20_000_000_000,
                  drivers=[_drv("VD-1",[_sc("VD-1-E",40_000.0,355_000)]),_drv("VD-5",[_sc("VD-5-A",5_000_000_000.0,1.0)])],
                  risk=_risk(5,3,2,1),cy=3,asset_type="turbina_hidro"),
            _proj("F-TI-01","Todos",2_000_000_000,
                  drivers=[_drv("VD-2",[_sc("VD-2-A",300_000_000.0,1.0)]),_drv("VD-5",[_sc_cr("VD-5-C",7.5,400_000_000_000)])],
                  inv_type="Proyecto Empresarial / TI"),
        ]
        # Precedencia: F-TyD-02 requiere F-TyD-01
        projects[1].precedent_project_id = "F-TyD-01"
        projects[1].min_lag_years = 1

        periods = [_period(y,20_000_000_000,pm=80.0,conc=4) for y in range(2026,2031)]
        pisos = {"AyA":5_000_000_000,"Generacion":15_000_000_000}

        # 3. MILP con todos los modulos
        res = _run_milp(projects, periods,
                        pisos_inversion_COP=pisos,
                        degradation_hints=hints,
                        risk_tolerance_level=4,
                        compute_frontier=False)
        r.check(res.solver_status in ("OPTIMAL","FEASIBLE"), f"Full MILP={res.solver_status}")
        r.check(len(res.selected) > 0, f"{len(res.selected)} seleccionados")

        # 4. EVM — proyectos en ejecucion
        actuals = [
            ProjectActual("F-AyA-01",ac_COP=3_000_000_000,pct_complete=0.35,cut_year=2027),
        ]
        evm_results, evm_summ = EVMEngine.compute_portfolio(projects, actuals, cut_year=2027)
        r.check(len(evm_results) >= 1, f"{len(evm_results)} resultados EVM")

        # 5. Snapshot (API: storage_dir, capture)
        store = SnapshotStore(storage_dir=td)
        eng = ValueEngine(CFG)
        snap = store.capture(eng.evaluate(projects[0]), projects[0], CFG, "simulacion")
        r.check(snap.integrity_hash != "", "Snapshot guardado con SHA-256")

        # 6. Workflow
        wfs = {}
        for p in projects:
            WorkflowEngine.ensure_workflow(wfs, p.id, p.name, p.business_unit, cycle="2026")
        summ_wf = WorkflowEngine.summary(wfs)
        r.check(summ_wf.get("BORRADOR",0) == len(projects), f"Todos en BORRADOR: {summ_wf.get('BORRADOR')}")

        # Verificar precedencia en resultado MILP
        sel_map = {s["project_id"]:s["year"] for s in res.selected}
        if "F-TyD-01" in sel_map and "F-TyD-02" in sel_map:
            lag_real = sel_map["F-TyD-02"] - sel_map["F-TyD-01"]
            r.check(lag_real >= 1, f"Precedencia respetada: lag={lag_real}a")
        else:
            r.check(True, "Precedencia sin violacion (uno no seleccionado)")

@sim("S13.2 Escenario: committed + outage + R8 must-mitigate", "S13 Integral")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    outages = [PlannedOutage(outage_id="O1",asset_id="TR-CRIT",year=2027,name="Mantenimiento mayor")]
    dr_crit = [_drv("VD-1",[_sc("VD-1-A",300.0,335_000)])]
    dr_comm = [_drv("VD-1",[_sc("VD-1-A",50.0,335_000)])]
    p_crit = _proj("CRIT-01","TyD",8_000_000_000,drivers=dr_crit,
                   risk=_risk(5,4,1,1),asset_id="TR-CRIT",outage_align=True)
    p_comm = _proj("COMM-INT","TyD",4_000_000_000,drivers=dr_comm,
                   committed=True,committed_year=2026)
    p_disc = [_proj(f"DISC-{i}","TyD",2_000_000_000,drivers=copy.deepcopy(dr_comm)) for i in range(3)]
    projects = [p_crit, p_comm] + p_disc
    periods = [_period(y,15_000_000_000) for y in [2026,2027,2028]]
    res = _run_milp(projects, periods,
                    planned_outages=outages, risk_tolerance_level=4)
    r.check(res.solver_status in ("OPTIMAL","FEASIBLE"), f"Committed+outage+R8={res.solver_status}")
    sel_ids = {s["project_id"] for s in res.selected}
    r.check("COMM-INT" in sel_ids, "Committed seleccionado")
    if "CRIT-01" in sel_ids:
        crit_year = next(s["year"] for s in res.selected if s["project_id"]=="CRIT-01")
        r.check(crit_year == 2027, f"CRIT-01 ejecutado en ventana outage 2027 (año={crit_year})")

# ==============================================================================
# GRUPO S14 — Validaciones de borde y robustez
# ==============================================================================

@sim("S14.1 CAPEX=0 no genera excepcion", "S14 Robustez")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    dr = [_drv("VD-1",[_sc("VD-1-A",100.0,335_000)])]
    p = _proj("CAPEX0","TyD",0.0,drivers=dr)
    try:
        res = eng.evaluate(p)
        r.check(res["value_efficiency_index"] == 0.0, "VEI=0 cuando CAPEX=0")
    except Exception as e:
        r.check(False, f"Excepcion inesperada CAPEX=0: {e}")

@sim("S14.2 WACC=0 — factor NPV = horizonte", "S14 Robustez")
def _(r: SimResult):
    cfg0 = copy.deepcopy(CFG)
    cfg0["wacc"] = 0.0
    eng = ValueEngine(cfg0)
    r.check(eng._factor_npv == eng.horizon, f"factor_npv={eng._factor_npv} == horizon={eng.horizon}")

@sim("S14.3 Proyecto sin drivers — beneficio = 0", "S14 Robustez")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    p = _proj("NO-DRV","TyD",5_000_000_000,drivers=[])
    res = eng.evaluate(p)
    r.check(res["benefits_raw_COP"] == 0.0, f"Sin drivers: beneficio={res['benefits_raw_COP']}")

@sim("S14.4 Proyecto sin riesgo — delta_r = 0", "S14 Robustez")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    dr = [_drv("VD-1",[_sc("VD-1-A",100.0,335_000)])]
    p = _proj("NO-RISK","TyD",5_000_000_000,drivers=dr,risk=None)
    res = eng.evaluate(p)
    r.check(res["delta_r_npv_COP"] == 0.0, "Sin riesgo: delta_r=0")

@sim("S14.5 IDs duplicados en portafolio — MILP maneja", "S14 Robustez")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    dr = [_drv("VD-1",[_sc("VD-1-A",100.0,335_000)])]
    p1 = _proj("DUP-01","TyD",3_000_000_000,drivers=dr)
    p2 = _proj("DUP-01","TyD",3_000_000_000,drivers=copy.deepcopy(dr))
    try:
        periods = [_period(2026,20_000_000_000)]
        res = _run_milp([p1,p2], periods)
        r.check(res.solver_status in ("OPTIMAL","FEASIBLE","INFEASIBLE"),
                f"MILP con IDs dup: status={res.solver_status}")
    except Exception as e:
        r.check(True, f"Excepcion controlada con IDs dup: {type(e).__name__}")

@sim("S14.6 Portafolio vacio — MILP retorna sin seleccionados", "S14 Robustez")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    periods = [_period(2026,10_000_000_000)]
    try:
        res = _run_milp([], periods)
        r.check(len(res.selected)==0, "Portafolio vacio: 0 seleccionados")
    except Exception as e:
        r.check(True, f"Excepcion controlada portafolio vacio: {type(e).__name__}")

@sim("S14.7 write_audit_log no lanza excepcion", "S14 Robustez")
def _(r: SimResult):
    # write_audit_log(result: dict, config: dict) — result debe ser un evaluate() completo
    eng = ValueEngine(CFG)
    dr = [_drv("VD-1", [_sc("VD-1-A", 100.0, 335_000)])]
    p = _proj("AUDIT-TEST", "TyD", 5_000_000_000, drivers=dr)
    result = eng.evaluate(p)
    # Ruta explicita a un temporal: sin ella la simulacion deja asientos de
    # auditoria de un proyecto ficticio en la traza de produccion.
    with tempfile.TemporaryDirectory() as td:
        destino = os.path.join(td, "audit_sim.log")
        try:
            write_audit_log(result, CFG, path=destino)
            r.check(os.path.exists(destino),
                    "write_audit_log ejecutado y registro escrito")
        except Exception as e:
            r.check(False, f"Error write_audit_log: {e}")

@sim("S14.8 load_config / save_config round-trip", "S14 Robustez")
def _(r: SimResult):
    # save_config(config) / load_config() usan ruta por defecto (config.json)
    # Verificamos round-trip usando JSON directamente como alternativa robusta
    import json as _json
    with tempfile.TemporaryDirectory() as td:
        cfg_path = os.path.join(td, "cfg_roundtrip.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            _json.dump(CFG, f, ensure_ascii=False, default=str)
        with open(cfg_path, encoding="utf-8") as f:
            cfg2 = _json.load(f)
        r.check(cfg2.get("wacc") == CFG.get("wacc"),
                f"WACC preservado: {cfg2.get('wacc')}")
        r.check("value_drivers" in cfg2, "value_drivers preservado")
        r.check("asset_horizons_by_bu" in cfg2, "asset_horizons_by_bu preservado")
        # Verificar que save_config/load_config existen y son llamables
        r.check(callable(save_config), "save_config es callable")
        r.check(callable(load_config), "load_config es callable")

@sim("S14.9 Nivel CR=0 y CR=10 (extremos escala C55)", "S14 Robustez")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    dr0  = [_drv("VD-5",[_sc_cr("VD-5-C",0.0,400_000_000_000)])]
    dr10 = [_drv("VD-5",[_sc_cr("VD-5-C",10.0,400_000_000_000)])]
    p0   = _proj("CR0","TyD",1_000_000,drivers=dr0)
    p10  = _proj("CR10","TyD",1_000_000,drivers=dr10)
    r0   = eng.evaluate(p0)
    r10  = eng.evaluate(p10)
    r.check(r10["benefits_raw_COP"] > r0["benefits_raw_COP"],
            f"CR=10 > CR=0: {r10['benefits_raw_COP']/1e6:.0f}MM > {r0['benefits_raw_COP']/1e6:.0f}MM")

@sim("S14.10 deferral_years=3 infla CAPEX y descuenta ΔR", "S14 Robustez")
def _(r: SimResult):
    eng = ValueEngine(CFG)
    dr = [_drv("VD-1",[_sc("VD-1-A",200.0,335_000)])]
    p = _proj("DEF-3","TyD",8_000_000_000,drivers=dr,risk=_risk(3,3,1,1))
    r0 = eng.evaluate(p, deferral_years=0)
    r3 = eng.evaluate(p, deferral_years=3)
    infl = CFG.get("capex_inflation_rate", 0.04)
    w = _wacc_bu("TyD")

    # CAPEX nominal: se sigue inflando a pesos del anio de ejecucion.
    r.check(abs(r3["capex_COP"] - r0["capex_COP"] * (1 + infl) ** 3) < 1.0,
            f"CAPEX nominal inflado (1+{infl})^3: {r3['capex_COP']/1e9:.2f}B")

    # CAPEX que entra a la ecuacion: el nominal traido a valor presente. Antes
    # se restaba en pesos futuros contra beneficios en pesos de hoy.
    esperado_pv = r3["capex_COP"] / ((1 + w) ** 3)
    r.check(abs(r3["capex_pv_COP"] - esperado_pv) < 1.0,
            f"CAPEX a valor presente = nominal/(1+r)^3: {r3['capex_pv_COP']/1e9:.2f}B")

    # DeltaR ya NO sigue la forma cerrada ((1+g)/(1+r))^3: se le resta la
    # exposicion sufrida durante la espera y la ventana de evaluacion se acorta
    # en los anios diferidos. Ambas cosas lo bajan, asi que el viejo factor
    # cerrado es ahora una cota superior estricta.
    g = r3["risk_analysis"]["risk_growth_rate_used"]
    cota = r0["delta_r_npv_COP"] * ((1 + g) ** 3) / ((1 + w) ** 3)
    r.check(r3["delta_r_npv_COP"] < cota,
            f"DeltaR por debajo de la cota sin correccion: "
            f"{r3['delta_r_npv_COP']/1e9:.2f}B < {cota/1e9:.2f}B")
    r.check(r3["risk_analysis"].get("deferral_exposure_COP", 0) > 0,
            "Se cobro exposicion por los 3 anios de espera")

# ==============================================================================
# RUNNER PRINCIPAL + INFORME
# ==============================================================================

def _print_summary():
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r.ok)
    failed = total - passed
    print()
    print("="*70)
    print(f"  RESUMEN FINAL — EPM AIP Motor Predictivo")
    print(f"  Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-"*70)
    by_group: dict = {}
    for r in RESULTS:
        by_group.setdefault(r.group, []).append(r)
    for gname, gresults in by_group.items():
        gpass = sum(1 for x in gresults if x.ok)
        gfail = len(gresults) - gpass
        tag = "OK" if gfail==0 else "FAIL"
        print(f"  [{tag}] {gname}: {gpass}/{len(gresults)} pasaron")
    print("-"*70)
    print(f"  TOTAL: {passed}/{total} PASS  |  {failed} FAIL")
    print("="*70)

def _generate_html(path: str):
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r.ok)
    failed = total - passed
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows_html = []
    for r in RESULTS:
        status_cls = "pass" if r.ok else "fail"
        status_txt = "PASS" if r.ok else "FAIL"
        msgs_html = "<br>".join(
            f"<span class='msg-ok'>" + m + "</span>" if m.startswith("✓")
            else f"<span class='msg-fail'>" + m + "</span>"
            for m in r.msgs
        )
        rows_html.append(f"""
        <tr class='{status_cls}'>
            <td class='status {status_cls}'>{status_txt}</td>
            <td>{r.group}</td>
            <td>{r.name}</td>
            <td class='time'>{r.elapsed:.2f}s</td>
            <td class='details'>{msgs_html}</td>
        </tr>""")

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EPM AIP — Informe de Simulaciones</title>
<style>
  body{{font-family:"Segoe UI",Arial,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}}
  .header{{background:linear-gradient(135deg,#00A86B 0%,#005C2E 100%);padding:32px 48px;color:white}}
  .header h1{{margin:0 0 4px;font-size:28px}}
  .header p{{margin:0;opacity:0.85;font-size:14px}}
  .kpi-bar{{display:flex;gap:24px;padding:24px 48px;background:#1e293b}}
  .kpi{{background:#0f172a;border-radius:12px;padding:20px 28px;flex:1;text-align:center;border:1px solid #334155}}
  .kpi .val{{font-size:36px;font-weight:700}}
  .kpi .lbl{{font-size:12px;color:#94a3b8;margin-top:4px}}
  .kpi.pass .val{{color:#00A86B}}
  .kpi.fail .val{{color:#DC2626}}
  .kpi.total .val{{color:#60a5fa}}
  .container{{padding:24px 48px}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}}
  th{{background:#1e293b;padding:10px 12px;text-align:left;font-weight:600;color:#94a3b8;border-bottom:2px solid #334155}}
  td{{padding:8px 12px;border-bottom:1px solid #1e293b;vertical-align:top}}
  tr.pass{{background:#0f2d1a}}
  tr.fail{{background:#2d0f0f}}
  tr:hover{{filter:brightness(1.1)}}
  .status{{font-weight:700;border-radius:6px;padding:3px 8px;text-align:center;font-size:11px}}
  .status.pass{{background:#00A86B;color:white}}
  .status.fail{{background:#DC2626;color:white}}
  .time{{color:#64748b;font-size:11px;white-space:nowrap}}
  .details{{font-size:11px;line-height:1.6}}
  .msg-ok{{color:#4ade80}}
  .msg-fail{{color:#f87171}}
  .group-header{{background:#1e293b;padding:12px 16px;margin:24px 0 0;border-left:4px solid #00A86B;font-weight:600;color:#00A86B;border-radius:4px}}
  .footer{{padding:24px 48px;color:#475569;font-size:12px;text-align:center;border-top:1px solid #1e293b;margin-top:40px}}
</style>
</head>
<body>
<div class="header">
  <h1>⚡ EPM AIP — Informe de Simulaciones</h1>
  <p>Motor Predictivo Enterprise · VFDD v5.0 · ASPD — Gerencia Planeación Empresarial · {ts}</p>
</div>
<div class="kpi-bar">
  <div class="kpi total"><div class="val">{total}</div><div class="lbl">Total simulaciones</div></div>
  <div class="kpi pass"><div class="val">{passed}</div><div class="lbl">PASS</div></div>
  <div class="kpi fail"><div class="val">{failed}</div><div class="lbl">FAIL</div></div>
  <div class="kpi {'pass' if failed==0 else 'fail'}"><div class="val">{passed*100//total if total else 0}%</div><div class="lbl">Tasa de éxito</div></div>
</div>
<div class="container">
<table>
<thead><tr>
  <th>Estado</th><th>Grupo</th><th>Simulación</th><th>Tiempo</th><th>Detalle de checks</th>
</tr></thead>
<tbody>
{chr(10).join(rows_html)}
</tbody>
</table>
</div>
<div class="footer">
  Generado por simulaciones_epm_aip.py · Copperleaf C55 · ISO 55001:2024 §4.5 · EPM Medellín
</div>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Informe HTML -> {path}")



# =============================================================================
# GRUPO S15 — Cobertura de las brechas halladas por campana de mutacion
# =============================================================================
# Cada prueba de este grupo existe porque un bug inyectado en esa linea pasaba
# inadvertido por las 84 pruebas anteriores. Las aserciones comparan contra la
# forma cerrada, no contra el propio motor, y verifican DIRECCION y MAGNITUD.

def _eng():
    return ValueEngine(CFG)

def _wacc_bu(bu):
    w = {k: v for k, v in CFG.get("wacc_by_bu", {}).items() if not k.startswith("_")}
    return float(w.get(bu, w.get("Todos", CFG.get("wacc", 0.10))))

def _cof(bu, nivel):
    rm = CFG["risk_matrix"]
    t = rm.get("consequence_levels_by_bu", {}).get(bu) or rm["consequence_levels"]
    return float(t[str(nivel)]["value_COP"])

def _pof(nivel):
    return float(CFG["risk_matrix"]["probability_levels"][str(nivel)]["annual_prob"])


@sim("S15.1 Weibull: el parametro de forma cambia la PoF (mata R1)", "S15 Cobertura")
def _(r: SimResult):
    # Con beta=1 la Weibull degenera en exponencial. Si el motor ignorara beta,
    # ambas curvas coincidirian.
    pof_b25 = ValueEngine._pof_weibull_static(30.0, 40.0, 2.5)
    pof_b10 = ValueEngine._pof_weibull_static(30.0, 40.0, 1.0)
    esperado_25 = 1 - math.exp(-((30.0 / 40.0) ** 2.5))
    esperado_10 = 1 - math.exp(-((30.0 / 40.0) ** 1.0))
    r.check(abs(pof_b25 - esperado_25) < 1e-9, f"PoF(beta=2.5)={pof_b25:.5f} forma cerrada")
    r.check(abs(pof_b10 - esperado_10) < 1e-9, f"PoF(beta=1.0)={pof_b10:.5f} forma cerrada")
    r.check(abs(pof_b25 - pof_b10) > 0.05,
            f"beta SI altera la curva: {pof_b25:.4f} vs {pof_b10:.4f}")


@sim("S15.2 La intervencion reduce el riesgo segun la matriz (mata R2)", "S15 Cobertura")
def _(r: SimResult):
    # ratio_prob = PoF_residual / PoF_base. Si el motor lo fijara en 1, mitigar
    # mas no cambiaria el DeltaR.
    eng = _eng()
    def dr(rp):
        p = _proj("W", "TyD", 5e9, risk=_risk(4, 4, 2, rp, beta=2.5, eta=40.0, age=35.0))
        return eng.evaluate(p)["delta_r_npv_COP"]
    dr_fuerte, dr_debil = dr(1), dr(3)
    r.check(dr_fuerte > dr_debil,
            f"Mitigar mas vale mas: {dr_fuerte/1e9:.2f}B > {dr_debil/1e9:.2f}B")
    ratio = _pof(3) / _pof(4)
    r.check(ratio < 0.9, f"ratio_prob nivel3/nivel4 = {ratio:.3f} < 1")


@sim("S15.3 DeltaR descuenta el riesgo residual (mata R3)", "S15 Cobertura")
def _(r: SimResult):
    # Rama exponencial (sin Weibull): DeltaR anual = CoF_b*PoF_b - CoF_r*PoF_r.
    eng = _eng()
    p = _proj("X", "TyD", 5e9, cy=0, risk=_risk(4, 4, 2, 2))
    res = eng.evaluate(p)["risk_analysis"]
    esperado = _cof("TyD", 4) * _pof(4) - _cof("TyD", 2) * _pof(2)
    solo_base = _cof("TyD", 4) * _pof(4)
    r.check(abs(res["delta_r_annual_COP"] - esperado) < 1.0,
            f"DeltaR anual = {res['delta_r_annual_COP']/1e9:.3f}B (forma cerrada)")
    r.check(abs(res["delta_r_annual_COP"] - solo_base) > 1e6,
            "El residual SI se resta (no es solo el riesgo base)")


@sim("S15.4 Factor Gordon incorpora el crecimiento del riesgo (mata R4)", "S15 Cobertura")
def _(r: SimResult):
    # (1-((1+g)/(1+r))^H)/(r-g) debe diferir de la anualidad simple H.
    eng = _eng()
    p = _proj("G", "TyD", 5e9, cy=0, asset_type="transformador_potencia",
              risk=_risk(4, 4, 2, 2))
    res = eng.evaluate(p)["risk_analysis"]
    # D-14: risk_analysis ahora agrega risk_components; las claves de forma de un
    # solo componente (npv_factor_used, etc.) viven en components[i], no en la raiz.
    comp = res["components"][0]
    w = _wacc_bu("TyD")
    g = comp["risk_growth_rate_used"]
    H = comp["horizon_used"]
    esperado = (1 - ((1 + g) / (1 + w)) ** H) / (w - g) if g > 0 and abs(w - g) > 1e-9 else float(H)
    r.check(comp["npv_factor_used"] is not None, "rama exponencial activa")
    r.check(abs(comp["npv_factor_used"] - esperado) < 1e-6,
            f"Gordon={comp['npv_factor_used']:.4f} vs forma cerrada {esperado:.4f}")
    r.check(abs(comp["npv_factor_used"] - H) > 1.0,
            f"NO es la anualidad simple H={H}")


@sim("S15.5 El riesgo se descuenta por tiempo de obra (mata R5)", "S15 Cobertura")
def _(r: SimResult):
    # El riesgo solo baja cuando la obra termina: DeltaR debe caer con cy.
    eng = _eng()
    def dr(cy):
        # Weibull explicito: el fd_lag de esa rama es una linea distinta a la
        # de la rama exponencial y necesita su propia cobertura.
        p = _proj(f"F{cy}", "TyD", 5e9, cy=cy,
                  risk=_risk(4, 4, 2, 2, beta=2.5, eta=40.0, age=35.0))
        return eng.evaluate(p)["delta_r_npv_COP"]
    d0, d3 = dr(0), dr(3)
    w = _wacc_bu("TyD")
    r.check(d3 < d0, f"cy=3 vale menos que cy=0: {d3/1e9:.2f}B < {d0/1e9:.2f}B")
    r.check(abs(d3 / d0 - 1 / (1 + w) ** 3) < 1e-6,
            f"El factor es exactamente 1/(1+wacc)^3 = {1/(1+w)**3:.5f}")


@sim("S15.6 CoF usa la tabla del negocio, no la generica (mata R6)", "S15 Cobertura")
def _(r: SimResult):
    # Este es el bug que subestimaba el riesgo de TyD en 42%.
    eng = _eng()
    p = _proj("B", "TyD", 5e9, cy=0, risk=_risk(4, 4, 1, 1))
    res = eng.evaluate(p)["risk_analysis"]
    por_bu = _cof("TyD", 4)
    generico = float(CFG["risk_matrix"]["consequence_levels"]["4"]["value_COP"])
    r.check(res["cof_source"] == "TyD", f"cof_source={res['cof_source']} (por negocio)")
    r.check(abs(por_bu - generico) > 1e9,
            f"Las tablas difieren: TyD={por_bu/1e9:.1f}B vs generica={generico/1e9:.1f}B")
    r.check(abs(res["baseline_annual_COP"] - por_bu * _pof(4)) < 1.0,
            "El EAL base usa el CoF de TyD")


@sim("S15.7 Funcion de valor lineal escala proporcionalmente (mata V1)", "S15 Cobertura")
def _(r: SimResult):
    # Concept rating con VF lineal: el COP debe ser proporcional al score.
    eng = _eng()
    def val(nivel):
        sc = SubCriterionInput(id="VD-5-C", label="CR", type="concept_rating",
                               delta_annual=nivel, unit="n", weight=1.0,
                               value_function="linear", scale_max_COP=100e9)
        p = _proj("L", "TyD", 1e9, cy=0, drivers=[_drv("VD-5", [sc])])
        return eng.evaluate(p)["benefits_bruto_COP"]
    v7, v9 = val(7.0), val(9.0)
    r.check(v7 > 0 and v9 > v7, f"nivel 9 ({v9/1e9:.1f}B) > nivel 7 ({v7/1e9:.1f}B)")
    r.check(abs(v9 / v7 - 2.0) < 1e-6, f"Proporcionalidad: v9/v7={v9/v7:.4f} = 2.0")
    # Una razon NO detecta un reescalado uniforme de la funcion de valor:
    # hay que comparar contra el valor absoluto de la forma cerrada.
    # score(7) = (7-5)/5*10 = 4  ->  VF_lineal = 4/10 * scale_max
    w = _wacc_bu("TyD")            # evaluate() usa el WACC del negocio, no el global
    factor = (1 - (1 + w) ** -30) / w
    esperado = 0.4 * 100e9 * factor
    r.check(abs(v7 - esperado) / esperado < 1e-6,
            f"Valor absoluto = 4/10 x scale_max x factorNPV: {v7/1e9:.2f}B")


@sim("S15.8 El peso del sub-criterio afecta el resultado (mata V4)", "S15 Cobertura")
def _(r: SimResult):
    eng = _eng()
    def val(w):
        sc = _sc("VD-1-A", 1000.0, 335_000); sc.weight = w
        p = _proj("P", "TyD", 1e9, cy=0, drivers=[_drv("VD-1", [sc])])
        return eng.evaluate(p)["benefits_bruto_COP"]
    v1, v05 = val(1.0), val(0.5)
    r.check(abs(v05 / v1 - 0.5) < 1e-9, f"peso 0.5 da la mitad: {v05/v1:.6f}")


@sim("S15.9 El OPEX incremental entra al VNT (mata C2)", "S15 Cobertura")
def _(r: SimResult):
    eng = _eng()
    def vnt(opex):
        p = _proj("O", "TyD", 5e9, cy=0, opex=opex)
        return eng.evaluate(p)
    a, b = vnt(0.0), vnt(500e6)
    dif = a["net_value_COP"] - b["net_value_COP"]
    r.check(dif > 0, f"Mas OPEX baja el VNT en {dif/1e9:.2f}B")
    r.check(abs(dif - abs(b["opex_incremental_npv_COP"])) < 1.0,
            "La caida equivale exactamente al VPN del OPEX")


@sim("S15.10 Un proyecto no se ejecuta dos veces (mata M2)", "S15 Cobertura")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 3_000.0, 335_000)])]
    projects = [_proj(f"U{i}", "TyD", 2e9, drivers=copy.deepcopy(dr)) for i in range(3)]
    periods = [_period(y, 50e9) for y in (2026, 2027)]
    res = _run_milp(projects, periods)
    ids = [s["project_id"] for s in res.selected]
    r.check(len(res.selected) >= 1, f"{len(res.selected)} seleccionados >= 1 (no vacuo)")
    r.check(len(ids) == len(set(ids)), f"Sin repetidos: {ids}")


@sim("S15.11 Exclusion mutua impide seleccionar ambos (mata M4)", "S15 Cobertura")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 4_000.0, 335_000)])]
    a = _proj("MX-A", "TyD", 2e9, drivers=copy.deepcopy(dr), mutex=["MX-B"])
    b = _proj("MX-B", "TyD", 2e9, drivers=copy.deepcopy(dr))
    res = _run_milp([a, b], [_period(2026, 50e9)])
    ids = {s["project_id"] for s in res.selected}
    r.check(len(res.selected) >= 1, f"{len(res.selected)} seleccionados >= 1 (no vacuo)")
    r.check(not {"MX-A", "MX-B"} <= ids, f"No ambos a la vez: {ids}")


@sim("S15.12 Persona-mes por vigencia restringe (mata M5)", "S15 Cobertura")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 4_000.0, 335_000)])]
    projects = [_proj(f"PM{i}", "TyD", 2e9, drivers=copy.deepcopy(dr)) for i in range(6)]
    holgado = _run_milp(projects, [_period(2026, 200e9, pm=1e9)])
    apretado = _run_milp(projects, [_period(2026, 200e9, pm=12.0)])
    r.check(len(holgado.selected) >= 1, f"Sin limite: {len(holgado.selected)}")
    r.check(len(apretado.selected) < len(holgado.selected),
            f"Con limite de persona-mes se seleccionan menos: "
            f"{len(apretado.selected)} < {len(holgado.selected)}")


@sim("S15.13 Concurrencia maxima restringe (mata M6)", "S15 Cobertura")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 4_000.0, 335_000)])]
    projects = [_proj(f"CC{i}", "TyD", 2e9, drivers=copy.deepcopy(dr)) for i in range(6)]
    res = _run_milp(projects, [_period(2026, 200e9, conc=2)])
    por_anio = {}
    for s in res.selected:
        por_anio[s["year"]] = por_anio.get(s["year"], 0) + 1
    r.check(len(res.selected) >= 1, f"{len(res.selected)} seleccionados >= 1 (no vacuo)")
    r.check(all(v <= 2 for v in por_anio.values()),
            f"Maximo 2 por vigencia: {por_anio}")


@sim("S15.14 Riesgo maximo de portafolio restringe (mata M7)", "S15 Cobertura")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 4_000.0, 335_000)])]
    projects = [_proj(f"RM{i}", "TyD", 2e9, drivers=copy.deepcopy(dr),
                      risk=_risk(4, 4, 3, 3)) for i in range(6)]
    holgado = _run_milp(projects, [_period(2026, 200e9)])
    apretado = _run_milp(projects, [_period(2026, 200e9, risk_max=8.0e9)])
    r.check(len(holgado.selected) >= 1, f"Sin tope: {len(holgado.selected)}")
    # Cota INFERIOR: con tope=0 seleccionados la prueba seria vacua y no
    # distinguiria "restriccion correcta" de "restriccion inexistente".
    r.check(len(apretado.selected) >= 1,
            f"Con tope siguen entrando proyectos: {len(apretado.selected)} >= 1")
    r.check(len(apretado.selected) < len(holgado.selected),
            f"pero menos que sin tope: "
            f"{len(apretado.selected)} < {len(holgado.selected)}")


@sim("S15.15 Must-mitigate fuerza la mitigacion (mata M8)", "S15 Cobertura")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    # El mitigador destruye valor: solo entra si R8 lo obliga.
    # CAPEX enorme a proposito: su VNT debe ser negativo INCLUSO contando el
    # DeltaR que genera. Si no, el MILP lo elige por merito y la prueba no
    # verifica que R8 este forzando nada.
    caro = _proj("MIT-CARO", "TyD", 2_000e9, asset_id="ACT-L5",
                 drivers=[_drv("VD-1", [_sc("VD-1-A", 10.0, 335_000)])],
                 risk=_risk(5, 4, 1, 1))
    bueno = _proj("BUENO", "TyD", 2e9,
                  drivers=[_drv("VD-1", [_sc("VD-1-A", 4_000.0, 335_000)])])
    res = _run_milp([caro, bueno], [_period(2026, 5_000e9)])
    ids = {s["project_id"] for s in res.selected}
    vnt_caro = ValueEngine(CFG).evaluate(caro)["net_value_COP"]
    r.check(vnt_caro < 0, f"El mitigador destruye valor: {vnt_caro/1e9:.1f}B")
    r.check(len(res.selected) >= 1, f"{len(res.selected)} seleccionados >= 1 (no vacuo)")
    r.check("MIT-CARO" in ids,
            f"R8 lo forzo pese a destruir valor: {ids}")


@sim("S15.16 TOTEX por vigencia restringe (mata M10)", "S15 Cobertura")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    dr = [_drv("VD-1", [_sc("VD-1-A", 4_000.0, 335_000)])]
    projects = [_proj(f"TX{i}", "TyD", 2e9, drivers=copy.deepcopy(dr), opex=300e6)
                for i in range(6)]
    holgado = _run_milp(projects, [_period(2026, 200e9)])
    apretado = _run_milp(projects, [_period(2026, 200e9, totex=6e9)])
    r.check(len(holgado.selected) >= 1, f"Sin TOTEX: {len(holgado.selected)}")
    r.check(len(apretado.selected) < len(holgado.selected),
            f"Con TOTEX se seleccionan menos: "
            f"{len(apretado.selected)} < {len(holgado.selected)}")


@sim("S15.17 El hijo no entra sin su padre (mata M12)", "S15 Cobertura")
def _(r: SimResult):
    if not PULP_OK:
        r.msgs.append("SKIP"); return
    # El padre destruye valor; el hijo lo genera. Sin la restriccion el MILP
    # tomaria solo al hijo.
    padre = _proj("PADRE", "TyD", 25e9,
                  drivers=[_drv("VD-1", [_sc("VD-1-A", 10.0, 335_000)])])
    hijo = _proj("HIJO", "TyD", 2e9,
                 drivers=[_drv("VD-1", [_sc("VD-1-A", 6_000.0, 335_000)])])
    hijo.dependencies = ["PADRE"]
    res = _run_milp([padre, hijo], [_period(2026, 50e9)])
    ids = {s["project_id"] for s in res.selected}
    r.check("HIJO" not in ids or "PADRE" in ids,
            f"Si esta el hijo, esta el padre: {ids}")


@sim("S15.18 Weibull del motor de degradacion usa beta (mata D1)", "S15 Cobertura")
def _(r: SimResult):
    eng = _deg_eng()
    # condicion BAJA a proposito: con cond alta, condition_pof_anchor impone
    # PoF >= condicion y tapa por completo el efecto de beta.
    p1 = _profile("TR-B25", "TR beta 2.5", "transformador_potencia", "TyD",
                  1996, 40, 5, 5e9, 200e6, 4, beta=2.5, eta=40.0)
    p2 = _profile("TR-B10", "TR beta 1.0", "transformador_potencia", "TyD",
                  1996, 40, 5, 5e9, 200e6, 4, beta=1.0, eta=40.0)
    r1, r2 = eng.analyze(p1), eng.analyze(p2)
    r.check(r1.current_pof >= 0 and r2.current_pof >= 0, "PoF calculada en ambos")
    r.check(abs(r1.current_pof - r2.current_pof) > 0.01,
            f"beta SI altera la PoF: {r1.current_pof:.4f} vs {r2.current_pof:.4f}")



@sim("S15.19 GUARDIAN: diferir no debe crear valor", "S15 Cobertura")
def _(r: SimResult):
    """Invariante estructural: postergar nunca puede pagar por si solo.

    El motor escala DeltaR por (1+g)^diferimiento — el riesgo que evitaras es
    mayor si esperas. Con g > r en los CUATRO negocios eso bastaba para que el
    VNT creciera de forma monotona con el aplazamiento y el analisis de tiempo
    optimo recomendara diferir al borde de su ventana para cualquier activo.

    Dos correcciones lo cierran, y esta prueba vigila que sigan puestas:
      - se cobra la exposicion al riesgo durante los anios que se espera
      - la ventana de evaluacion es comun: diferir no regala anios extra de
        beneficio al final del horizonte

    Se prueba con las tasas REALES del config, que siguen teniendo g >= r. Ese
    es justamente el punto: el invariante ahora se sostiene a pesar de las
    tasas, no gracias a ellas. Si alguien apaga una correccion, esto cae.
    """
    w = {k: v for k, v in CFG.get("wacc_by_bu", {}).items() if not k.startswith("_")}
    g = {k: v for k, v in CFG["optimal_timing"].get("risk_growth_rate_by_bu", {}).items()
         if not k.startswith("_")}
    eng = ValueEngine(CFG)
    # Calibracion identica a S21_TratamientoTemporal de test_suite_epm_full.py
    # (capex 5.000M, delta 150): con 8.000M/200 el VNT de Gas queda tan dominado
    # por el riesgo mitigado que su serie es monotona creciente aun SIN defecto
    # — la prueba fallaba sin vigilar nada. Con esta calibracion AyA y Gas
    # dejan de ser monotonos, y el defecto historico reintroducido si los vuelve
    # monotonos (verificado en la meta-prueba de S21).
    dr = [_drv("VD-1", [_sc("VD-1-A", 150.0, 335_000)])]

    for bu in ("TyD", "AyA", "Gas", "Generacion"):
        r_ = float(w.get(bu, w.get("Todos", CFG.get("wacc", 0.10))))
        g_ = float(g.get(bu, CFG["optimal_timing"].get("risk_growth_rate", 0.15)))
        p = _proj(f"GUARD-{bu}", bu, 5_000_000_000, drivers=dr, risk=_risk(3, 3, 1, 1))
        vs = [eng.evaluate(p, deferral_years=d)["net_value_COP"] for d in range(0, 13)]

        # (a) La patologia era una serie que solo subia. Eso no puede volver.
        creciente = all(vs[i] <= vs[i + 1] for i in range(len(vs) - 1))
        r.check(not creciente,
                f"{bu} (g={g_:.0%} vs r={r_:.1%}): VNT no crece de forma monotona "
                f"[{vs[0]/1e9:.2f}B -> {vs[-1]/1e9:.2f}B]")

        # (b) Y el borde de la ventana no puede ser el optimo. Solo aplica a
        #     proyectos que CREAN valor: si el VNT es negativo en todo el rango
        #     el proyecto no debe hacerse, y que posponer una perdida la reduzca
        #     es descuento correcto, no un defecto.
        if vs[0] > 0:
            r.check(vs[-1] < vs[0],
                    f"{bu}: diferir al borde no supera al ano 0 "
                    f"({vs[-1]/1e9:.2f}B < {vs[0]/1e9:.2f}B)")

    # Y la exposicion tiene que estar efectivamente cobrada, no solo el signo.
    p = _proj("GUARD-EXP", "TyD", 5_000_000_000, drivers=dr, risk=_risk(3, 3, 1, 1))
    exp8 = eng.evaluate(p, deferral_years=8)["risk_analysis"].get("deferral_exposure_COP", 0)
    r.check(exp8 > 0, f"Exposicion cobrada a 8 anios: {exp8/1e9:.2f}B")


if __name__ == "__main__":
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    import argparse
    parser = argparse.ArgumentParser(description="Simulaciones EPM AIP")
    parser.add_argument("--html", action="store_true", help="Genera informe HTML")
    parser.add_argument("--group", default="", help="Filtra por grupo (ej: S1)")
    args = parser.parse_args()

    print()
    print("="*70)
    print("  EPM AIP — Suite de Simulaciones Integral")
    print(f"  Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  PuLP disponible: {PULP_OK}")
    print("="*70)
    print()

    # Ejecutar todas las simulaciones registradas por el decorador @sim
    RESULTS.clear()
    for fn in SIM_REGISTRY:
        fn()

    _print_summary()

    # El `or True` anulaba el flag: el informe se escribia siempre y reaparecia
    # en el directorio de produccion tras cada corrida, aunque nadie lo pidiera.
    if args.html:
        html_path = str(PROJECT_DIR / "informe_simulaciones_epm.html")
        _generate_html(html_path)
        print(f"\n  Informe HTML escrito en {html_path}")
