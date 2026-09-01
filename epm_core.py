"""
epm_core.py — Motor AIP EPM Medellín

Implementa la ecuación maestra de valor del marco AIP:

    VNT(i) = Σ(w_r × VD_r × FD_lag(r)) − CoD + ΔR − CAPEX_VP − ΔOPEX_NPV

Define el comportamiento algorítmico y los resultados numéricos exigidos a la
solución. La forma en que este archivo está organizado NO es una referencia de
arquitectura: es un prototipo de calibración, sin interfaz, sin persistencia y
sin capacidad de escala. Lo exigible es la matemática y los valores que produce,
verificables con test_suite_epm_full.py.

La parametrización calibrada vive en config.json y siempre gana sobre los
valores de reserva de este módulo.

Contenido:
  BLOQUE 1  — Configuración DEFAULT_CONFIG e INVESTMENT_TYPES
  BLOQUE 2  — Dataclasses: SubCriterionInput, DriverInput, RiskInput,
               CostInput, ProjectInput (con temporalidad + precedencias)
  BLOQUE 3  — ValueEngine: ecuación maestra C55, Optimal Timing,
               Concept Rating, _get_risk_growth_rate por BU
  BLOQUE 4  — CLI helpers (Colors, ask*, ProjectWizard)
  BLOQUE 5  — InterventionEngine, BudgetPeriod, PortfolioOptimizer (scipy)
  BLOQUE 6  — SensitivityAnalyst
  BLOQUE 7  — DependencyError, DependencyEngine (deadlock + cascade CoD)
  BLOQUE 8  — ResourcePeriod, EnterprisePortfolioResult, EnterpriseOptimizer (PuLP)
  BLOQUE 9  — AssetProfile, DegradationModel, DegradationResult,
               AssetDegradationEngine (Weibull/Lineal/Escalón/Acelerado)
  BLOQUE 10 — EvaluationSnapshot, SnapshotStore (ISO 55001 §4.5 SHA-256)
  BLOQUE 11 — PortfolioLoader: carga Excel/CSV → ProjectInput[]
  BLOQUE 12 — print_report, write_audit_log, load_config, save_config

Uso desde Streamlit:
    from epm_core import (ValueEngine, EnterpriseOptimizer, AssetDegradationEngine,
                          SnapshotStore, DependencyEngine, PortfolioLoader,
                          DEFAULT_CONFIG, ProjectInput)

Uso desde CLI:
    python epm_core.py   (lanza el menú interactivo original)
"""
from __future__ import annotations
import sys, os, json, hashlib, uuid, math, copy, csv, io
import re as _re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple, Any
from pathlib import Path


# ── Consola: tolerar caracteres no representables ─────────────────────────────
# El motor imprime ─ ═ → ✓ ★ ℹ en reportes y trazas. En consolas Windows con
# cp1252 esos caracteres lanzan UnicodeEncodeError y abortan la corrida — es
# decir, una optimización válida se cae por un carácter de un mensaje de log.
# Se intenta UTF-8 y, si no es posible, se degrada a reemplazo silencioso.
# Nunca debe fallar: un problema de consola no puede tumbar el cálculo.
def _make_console_safe() -> None:
    for _stream in (sys.stdout, sys.stderr):
        _enc = (getattr(_stream, "encoding", None) or "").lower().replace("-", "")
        if _enc in ("utf8", "utf8mb4"):
            continue
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                _stream.reconfigure(errors="replace")
            except Exception:
                pass   # stream sin reconfigure (StringIO en tests) — se deja igual


_make_console_safe()


# ── Contrato de estado del solver ─────────────────────────────────────────────
# Los dos optimizadores hablaban vocabularios distintos: el enterprise devolvía
# la etiqueta de PuLP ('Optimal', 'Infeasible') y el de scipy devolvía el mensaje
# libre de HiGHS. Los consumidores —app, reportes, versionado de plan— no podían
# comparar contra nada estable. Se normaliza a un vocabulario único y documentado.
SOLVER_STATUS = ("OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNBOUNDED",
                 "NOT_SOLVED", "ERROR")


def normalize_solver_status(raw: Any) -> str:
    """Traduce el estado de cualquier solver al vocabulario de SOLVER_STATUS."""
    s = str(raw or "").strip().lower()
    if not s:
        return "NOT_SOLVED"
    if "optimal" in s:
        return "OPTIMAL"
    if "infeasible" in s or "infactible" in s:
        return "INFEASIBLE"
    if "unbounded" in s or "no acotado" in s:
        return "UNBOUNDED"
    if "not solved" in s or "undefined" in s or "no resuelto" in s:
        return "NOT_SOLVED"
    if "feasible" in s or "time limit" in s or "iteration limit" in s:
        # Solución válida encontrada pero no probada óptima (corte por tiempo).
        return "FEASIBLE"
    if "error" in s or "fail" in s:
        return "ERROR"
    return "ERROR"


# ── PuLP ──────────────────────────────────────────────────────────────────────
try:
    import pulp
    PULP_OK = True
except ImportError:
    PULP_OK = False
    pulp = None  # type: ignore

# ── numpy (opcional) ──────────────────────────────────────────────────────────
try:
    import numpy as np
    NP_OK = True
except ImportError:
    NP_OK = False
    np = None  # type: ignore

# ── openpyxl / scipy ─────────────────────────────────────────────────────────
try:
    import openpyxl
    OPENPYXL_OK = True
except ImportError:
    OPENPYXL_OK = False

try:
    from scipy.optimize import milp as scipy_milp, LinearConstraint, Bounds
    SCIPY_OK = True
except ImportError:
    SCIPY_OK = False



# Valores de reserva del motor. NO son la parametrización calibrada: esa vive
# en config.json y siempre gana sobre estos (ver _merge_config_defaults). La
# versión sigue el esquema de config.json para que una fusión entre ambos no
# quede reportando versiones distintas del mismo esquema.
DEFAULT_CONFIG = {
    "_metadata": {
        "version": "3.8.0",
        "author": "ASPD — Gerencia Planeación EPM",
        "last_updated": "2026-08-20",
        "approved_by": "Pendiente — sesión calibración ASPD",
        "referentes": "ISO 55001:2024 §4.5 · IAM SSG · Copperleaf C55"
    },
    "_metodologia": {
        "ecuacion_maestra": "VNT(i) = Σ(w_r × VD_r × FD_lag(r)) − CoD + ΔR − CAPEX_VP − ΔOPEX_NPV",
        "pesos_driver": "w_r aplicado en _calc_driver() nivel driver. Default 1.0 (ponderación emergente). Cambio requiere aprobación ASPD documentada en config.json.",
        "pesos_subcriterio": "weight = 1.0 universal para sub-criterios. Ponderación 100% emergente de monetización regulatoria.",
        "costos": "CAPEX y OPEX incremental con weight = -1.0 (flujos de salida).",
        "concept_rating": "Escala 0-6, nivel 3 = Caso Base. Curva-S simétrica.",
        "optimizador": "MILP exacto via scipy HiGHS. Maximiza Net Value total del portafolio.",
        "optimal_timing": "horizon_years, risk_growth_rate y capex_inflation_rate leídos de config.json. CoD calculado solo sobre beneficios de drivers (sin doble conteo de ΔR).",
        "trazabilidad": "Cada ejecución registrada en epm_audit.log (ISO 55001 §4.5)."
    },
    "wacc": 0.10,   # fallback global — usado si BU no está en wacc_by_bu
    # ── WACC por Unidad de Negocio ────────────────────────────────────────────
    # Copperleaf C55 §3.2: cada BU tiene perfil de riesgo regulatorio distinto.
    # Prioridad: wacc_by_bu[BU] > wacc_by_bu["Todos"] > wacc global.
    # Fuente referencia: CREG (TyD/Gas), CRA (AyA), mercado spot (Generacion).
    # CALIBRAR con WACC regulado vigente antes de cada ciclo PAI.
    "wacc_by_bu": {
        "_nota": "WACC efectivo por BU. Sobreescribe el WACC global para todos los cálculos NPV/EAV/CoD.",
        "TyD":        0.095,   # CREG — retorno regulado distribución y transmisión
        "Generacion": 0.110,   # Prima mercado — activos spot/PPA, mayor riesgo precio
        "AyA":        0.090,   # CRA — infraestructura agua, menor riesgo regulatorio
        "Gas":        0.095,   # CREG — distribución gas natural regulada
        "Todos":      0.100,   # Fallback si BU no está definida explícitamente
    },
    "analysis_horizon_years": 10,

    # ── Horizontes de vida útil por BU y tipo de activo ──────────────────────
    # Fuentes de referencia (pendiente calibración con política activos EPM/ASPD):
    #   Eléctrico:  IEC 60076-7 (transformadores, guía de carga/vida), IEEE C37 (interruptores),
    #               CIGRE TB 379 (cables), IEC 62271 (equipos AT)
    #   Hidráulico: ASCE Manuals (presas/penstock), normativa ANLA Colombia
    #   Gas:        ASME B31.8 (gasoductos), NTC-3728
    #   Agua:       AWWA M11/M23 (tuberías), Res. CRA 688/2014
    #   TI/SCADA:   Gartner lifecycle, NERC CIP
    # Jerarquía ValueEngine.get_horizon_for_asset():
    #   1. asset_horizons_by_bu[BU][asset_type]  — más específico
    #   2. asset_horizons_by_bu[BU]['_default']  — fallback por BU
    #   3. analysis_horizon_years                — fallback global
    # Estado: REFERENCIA IEEE/IEC/CREG — calibrar en sesión ASPD con historial EPM.
    "asset_horizons_by_bu": {
        "TyD": {
            "_default":                  15,    # Activo eléctrico genérico TyD
            # ── Transformadores ──
            "transformador_potencia":    30,    # IEC 60076-7: guía de carga/vida; convención de diseño 30-40a
            "transformador_distribucion": 25,   # CIGRE: distribución < AT tiene menor vida
            "transformador_medida":      20,    # TPs/TCs: menor vida por ciclaje dieléctrico
            # ── Interruptores y seccionadores ──
            "interruptor_at":            25,    # IEC 62271-100: 30 operaciones × ciclo vida
            "interruptor_mt":            20,    # Menor tensión, más operaciones por falla
            "seccionador":               25,    # IEC 62271-102
            "reconectador":              20,    # IEEE C37.60
            # ── Cables ──
            "cable_mt":                  30,    # XLPE típico TyD: 30-35a (IEC 60502)
            "cable_at":                  35,    # Cables AT enterrados: 35-40a
            "cable_bt":                  25,    # Red BT aérea/subterránea
            # ── Líneas y estructuras ──
            "linea_transmision":         40,    # Torres metálicas: 40-50a (RETIE Colombia)
            "linea_distribucion":        30,    # Postes/red MT aérea
            "subestacion_civil":         50,    # Obra civil: 50a mínimo
            # ── Protecciones y control ──
            "rele_proteccion":           15,    # IED digital: obsolescencia ~15a (IEC 61850)
            "sistema_scada":             12,    # Gartner: sistemas SCADA/DMS 10-15a
            "medidor_inteligente":       15,    # AMI: vida regulatoria Res. CREG 038/2014
            # ── Condensadores y reactivos ──
            "banco_condensadores":       20,    # IEEE 18: condensadores de potencia
            "reactancia":                25,    # Reactores de potencia
        },
        "Generacion": {
            "_default":                  25,    # Activo generación genérico
            # ── Presas y obra civil ──
            "presa_concreto":            80,    # ICOLD: presas concreto 80-100a
            "presa_tierra":              60,    # ASCE: presas tierra/escollera 60-80a
            "subestacion_civil":         50,
            # ── Turbinas y generadores ──
            "turbina_hidro":             40,    # IEC 60193: turbinas hidráulicas 35-45a
            "generador_sincrono":        40,    # IEEE C50.12: generadores sincrónicos
            "turbina_gas":               25,    # GE/Siemens lifecycle: ~25a con overhaul
            "turbina_vapor":             30,    # ASME: ciclo combinado 30a
            # ── Conducciones hidráulicas ──
            "tuberia_conduccion":        50,    # Penstock acero: 40-60a (ASCE Manuals 79)
            "compuerta_hidraulica":      40,    # ICOLD: 35-45a con mantenimiento
            "valvula_mariposa":          30,    # ASME B16.34
            # ── Transformadores de planta ──
            "transformador_potencia":    30,    # IEC 60076-7 (guía de carga/vida)
            "transformador_elevador":    35,    # Mayor rated power, mejor diseño térmico
            # ── Control y protección ──
            "sistema_scada":             12,
            "rele_proteccion":           15,
            "sistema_excitacion":        20,    # Excitadores estáticos
            "gobernador":                20,    # Gobernadores digitales
            # ── Solar y eólica ──
            "panel_solar":               25,    # IEC 61215: garantía 25a degradación < 20%
            "aerogenerador":             25,    # IEC 61400: diseño 20-25a
            "inversor":                  15,    # Electrónica de potencia: 15a típico
        },
        "AyA": {
            "_default":                  30,    # Activo agua y saneamiento genérico
            # ── Tuberías ──
            "tuberia_ac":                20,    # Asbesto-cemento: vida residual reducida
            "tuberia_pvc":               35,    # AWWA C900: PVC 35-50a según presión
            "tuberia_hdpe":              50,    # AWWA C906: HDPE alta resistencia
            "tuberia_hierro_ductil":     50,    # AWWA C151: hierro dúctil 50a+
            "tuberia_acero":             40,    # AWWA C200: acero con recubrimiento
            "tuberia_gres":              40,    # Colectores alcantarillado
            "tuberia_concreto":          40,    # RCC/CC: alcantarillado mayor
            # ── Equipos electromecánicos ──
            "bomba_centrifuga":          20,    # AWWA M17: bombas 15-25a según operación
            "bomba_vertical":            20,
            "valvula_control":           20,    # Válvulas de control: 15-25a
            "valvula_compuerta":         25,    # Válvulas de aislamiento
            "macromedidor":              15,    # Medición caudal: vida metrológica 15a
            # ── Plantas de tratamiento ──
            "planta_potabilizacion":     25,    # Civil 50a; electromecánico 20-25a → 25a
            "planta_ptar":               25,    # PTAR: obra civil 40a, eq. 20a → mix 25a
            "filtro":                    20,    # Filtros arena/antracita
            # ── Almacenamiento ──
            "tanque_concreto":           50,    # Tanques de almacenamiento concreto
            "tanque_metalico":           30,    # Tanques metálicos con recubrimiento
            # ── Control ──
            "sistema_scada":             12,
            "medidor_inteligente":       15,
        },
        "Gas": {
            "_default":                  30,    # Activo gas natural genérico
            # ── Gasoductos ──
            "gasoducto_acero":           40,    # ASME B31.8: acero con catódica 40-50a
            "gasoducto_pe":              40,    # Polietileno: ASME B31.8 40-50a
            "red_distribucion_pe":       40,    # PE-80/PE-100 distribución
            "red_distribucion_acero":    35,
            # ── Equipos de regulación y medición ──
            "estacion_regulacion":       25,    # ERP/ERM: electromecánico + civil
            "valvula_control":           20,    # ASME B16.34
            "medidor_gas":               15,    # Vida metrológica CREG/SIC: 10-15a
            "caudalimetro_ultras":        15,
            # ── Almacenamiento ──
            "esfera_gnl":                40,    # Esferas GNL/GNC acero especial
            "tanque_gnc":                20,    # Cilindros alta presión: 20a por norma
            # ── Compresión ──
            "compresor_reciprocante":    20,    # API 618: 20-25a
            "compresor_centrifugo":      25,    # API 617: 25-30a
            # ── Control ──
            "sistema_scada":             12,
            "sistema_telemetria":        12,
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    # INCERTIDUMBRE DE ESTIMACIÓN DEL COSTO — por alternativa
    # ══════════════════════════════════════════════════════════════════════════
    # Qué incertidumbre trata: qué tan bien se conoce el CAPEX de UNA alternativa.
    # Es epistémica y propia de cada alternativa: dos alternativas de la misma
    # necesidad pueden tener madurez de estimativo muy distinta (oferta en firme
    # vs. analogía). Es la incertidumbre que decide si la comparación ENTRE
    # alternativas está resuelta por los datos.
    #
    # No confundir con:
    #   · Incertidumbre de condición del activo → tratada por Weibull PoF(t) en RiskInput.
    #   · Incertidumbre de presupuesto          → tratada por la frontera eficiente.
    #   · Incertidumbre de supuestos del marco  → tratada por SensitivityAnalyst (WACC, horizonte).
    #
    # Rangos asimétricos: los costos de proyecto se desvían hacia arriba más de lo
    # que se desvían hacia abajo (alcances que crecen, permisos, cantidades
    # subestimadas). Un rango simétrico introduce sesgo optimista por construcción.
    "cost_estimate_uncertainty": {
        "_nota": (
            "Bandas de desviación del CAPEX según la base sobre la que se construyó "
            "el estimativo. Se aplican POR ALTERNATIVA vía CostInput.estimate_class. "
            "Valores de arranque tomados de AACE International RP 18R-97 — son "
            "referencia de industria, NO calibración EPM. Se reemplazan por las "
            "bandas propias a medida que EVMEngine acumule desviaciones reales "
            "(estimativo inicial vs. costo final por clase, negocio y tipo de intervención)."
        ),
        "_fuente": "AACE International RP 18R-97 'Cost Estimate Classification System'",
        "_calibrado_con_datos_epm": False,
        "_sesgo_recomendado": (
            "Ante duda, preferir bandas AMPLIAS. Declarar resuelta una decisión que "
            "está en traslape lleva a comprometer presupuesto en la alternativa "
            "equivocada; declarar en traslape una que sí estaba resuelta solo cuesta "
            "una revisión adicional del estimativo."
        ),
        # low  = desviación hacia abajo (negativa) · high = desviación hacia arriba (positiva)
        "classes": {
            "Class1": {
                "label":       "Clase 1 — Oferta en firme o contrato adjudicado",
                "description": "Precio contractual o ingeniería de detalle >60%.",
                "low": -0.05, "high": +0.10,
            },
            "Class2": {
                "label":       "Clase 2 — Cotización de proveedor / ingeniería básica",
                "description": "FEED 30-60% completo. Cantidades firmes.",
                "low": -0.10, "high": +0.15,
            },
            "Class3": {
                "label":       "Clase 3 — Ingeniería conceptual con cantidades de obra",
                "description": "Estándar de presupuestación.",
                "low": -0.15, "high": +0.20,
            },
            "Class4": {
                "label":       "Clase 4 — Prefactibilidad / presupuesto preliminar",
                "description": "Basado en estudios iniciales.",
                "low": -0.20, "high": +0.30,
            },
            "Class5": {
                "label":       "Clase 5 — Analogía, costo unitario o juicio experto",
                "description": "Sin ingeniería. Alta incertidumbre.",
                "low": -0.30, "high": +0.50,
            },
        },
        # Clase asumida cuando la alternativa no la declara.
        # Deliberadamente la más débil: quien quiera que su alternativa compita con
        # menos incertidumbre debe declarar por qué su cifra es sólida.
        "default_class": "Class5",
        # Bandas propias por negocio, cuando existan. Misma estructura que "classes".
        # Vacío = usar las de referencia. Se pueblan desde el histórico de EVMEngine.
        "classes_by_bu": {},
        # Umbral de traslape (fracción de la banda de la ganadora) por encima del
        # cual la elección entre alternativas se declara NO RESUELTA por los datos.
        "overlap_threshold": 0.0,
        "_overlap_note": (
            "0.0 = cualquier traslape marca la decisión como no resuelta. "
            "Subirlo tolera traslapes menores. No es un semáforo: el reporte siempre "
            "entrega el porcentaje crudo de traslape junto al veredicto."
        ),
    },

    # ══════════════════════════════════════════════════════════════════════════
    # MOTOR DE DEGRADACIÓN — incertidumbre de condición del activo
    # ══════════════════════════════════════════════════════════════════════════
    # Este bloque faltaba por completo en DEFAULT_CONFIG: existía solo en
    # config.json, así que cualquier corrida sobre los defaults dejaba el
    # motor de degradación sin calibración y sin que nadie se enterara.
    "degradation_engine": {
        "_nota": (
            "Parámetros del motor predictivo de degradación. La consecuencia "
            "monetaria de la falla NO se define aquí: se lee de risk_matrix, "
            "que es la fuente única institucional. Duplicarla producía dos "
            "valuaciones distintas de la misma falla."
        ),
        "consequence_source": "risk_matrix",
        "_consequence_source_note": (
            "risk_matrix = usa consequence_levels_by_bu[BU] y, si el negocio no "
            "está, consequence_levels. 'builtin' = usa la tabla de respaldo del "
            "código, solo para pruebas sin configuración."
        ),
        "opex_growth_rate": 0.08,
        "_opex_growth_note": (
            "Crecimiento anual del OPEX por envejecimiento, sobre el OPEX base. "
            "Referencia de industria; calibrar con el histórico de mantenimiento "
            "correctivo de EPM."
        ),
        "condition_pof_anchor": 1.0,
        "_condition_anchor_note": (
            "Factor que convierte current_condition (0=nuevo, 1=falla inminente) "
            "en piso de PoF. Con 1.0, una condición declarada de 0.8 impone "
            "PoF ≥ 0.8. El valor anterior de 0.5 hacía que un activo declarado "
            "en falla inminente solo alcanzara PoF 0.5, contradiciendo la "
            "inspección que lo declaró."
        ),
        "eeol_replacement_threshold": 0.85,
        "_eeol_threshold_note": (
            "Fracción del costo de reemplazo que el costo acumulado de mantener "
            "debe superar para declarar el fin de vida económica."
        ),
        # Sobrescriben el perfil cuando el activo no declara valor propio.
        # Formato: {"clase": {"beta": 2.5, "_eta_años": 40, "_fuente": "..."}}
        "weibull_beta_by_class": {},
        "consequence_by_class": {},
    },

    "optimal_timing": {
        "_nota": "Parámetros para el Análisis de Tiempo Óptimo de Intervención (Copperleaf Economic End-of-Life).",
        "horizon_years": 5,
        # Ventana de escaneo timing por BU. Refleja la vida útil diferencial de los activos:
        #   TyD: 10a (2× período CREG; IEEE C57/C37)
        #   Generacion: 15a (IHA; turbinas/penstocks evaluados a 25-35a)
        #   AyA: 15a (AWWA M27; tuberías β=1.8 → t* tardío)
        #   Gas: 12a (ASME B31.8; gasoductos β=1.6 + válvulas β=2.8)
        # Jerarquía: horizon_years_by_bu[BU] > horizon_years (global fallback)
        "horizon_years_by_bu": {
            "_nota": (
                "Ventana de escaneo t=0..N para encontrar t*=argmax VNT(t) por BU. "
                "Overrides horizon_years global. "
                "Derivada de: (1) vida útil modal Weibull por activo dominante, "
                "(2) período regulatorio × 2.5 ciclos, (3) guías sectoriales "
                "(IEEE C57/C37 para TyD, IHA para Generacion, AWWA M27 para AyA, ASME B31.8 para Gas). "
                "Calibrar con historial de t* observados en sesiones ASPD EPM."
            ),
            # TyD: transformadores (η=40a,β=2.5), cables MT (η=30a,β=2.2), interruptores (η=35a,β=3.0)
            # risk_growth 10-15%/a → t* típico 2-6a. 2× período CREG (4a) = 8a mínimo → 10a con margen.
            # Ref: IEC 60076-7 (transformadores, carga/vida), IEEE C37.10 (interruptores)
            "TyD":        10,

            # Generacion: turbinas (η=40a,β=2.0), penstocks (η=60a,β=2.0), presas (η=80a+,β=1.5)
            # Activos evaluados a los 25-35a → t* puede estar en año 8-14.
            # IHA "Hydropower Sustainability Assessment Protocol": ventana ≥15 años para activos civiles.
            # Ref: IHA 2021, USACE EP 1130-2-510, CEATI Hydropower Plant Equipment
            "Generacion": 15,

            # AyA: tuberías conducción (η=50a,β=1.8), bombas (η=20a,β=3.5), PTAR (η=35a,β=2.2)
            # Tuberías con β=1.8 tienen degradación lenta → t* hasta año 12-14 si el activo tiene 35a.
            # AWWA M27 / M11: ciclos de rehabilitación de redes en horizonte 15-25 años.
            # Ref: AWWA M27 Ed.3, MDPE/AC pipe lifecycle studies
            "AyA":        15,

            # Gas: gasoductos (η=55a,β=1.6), válvulas (η=25a,β=2.8), compresores (η=20a,β=3.0)
            # Gasoductos β=1.6 → muy lenta → t* puede ser año 6-10.
            # ASME B31.8-2022 §851: programas de integridad en ventana 5-15 años.
            # 12a cubre gasoductos de media edad y válvulas/compresores jóvenes.
            "Gas":        12,
        },
        "risk_growth_rate": 0.15,
        "_risk_growth_note": "Tasa anual de crecimiento del riesgo base por degradación (15% = activo eléctrico típico). Ajustar por clase de activo en sesión ASPD.",
        "capex_inflation_rate": 0.04,
        "_capex_inflation_note": "Incremento anual del CAPEX por postergación (4% = IPC + sobrecosto emergencia). Revisable con datos históricos EPM.",
        # Tasa de degradación por tipo de activo (derivada de β Weibull)
        # Jerarquía: by_asset > by_bu > global. Calibrar con historial EPM/ASPD.
        "risk_growth_rate_by_asset": {
            "_nota": "Tasas derivadas de β Weibull por familia de activo. Overrides by_bu. Calibrar con historial fallas EPM.",
            "transformador_potencia": 0.12,   # β≈2.5 — desgaste gradual dieléctrico
            "tuberia_conduccion":     0.08,   # β≈1.8 — corrosión lenta
            "interruptor":            0.15,   # β≈3.0 — desgaste mecánico acelerado
            "cable_mt":               0.10,   # β≈2.2 — degradación aislamiento
            "scada_sistema":          0.22,   # β≈4.0 — obsolescencia tecnológica rápida
            "bomba_centrifuga":       0.18,   # β≈3.5 — cavitación y desgaste
            "valvula_control":        0.14,   # β≈2.8 — desgaste asiento/vástago
        },
        # C55 — Parámetros Weibull por tipo de activo EPM
        # Fuentes de referencia (pendiente calibración con historial fallas EPM/ASPD):
        #   IEC 60076-7 (transformadores, carga/vida),  IEEE C37.10 (interruptores)
        #   CIGRE TB 379 (cables MT/AT),          ASME B31.8 (gasoductos)
        #   AWWA M11/M23 (tuberías),              Gartner IT lifecycle
        # Uso: si RiskInput.weibull_beta == 0 y asset_type coincide con una clave aquí,
        #       ValueEngine._get_weibull_params_for_asset() devuelve estos valores.
        # NOTA: eta (vida característica) calibrada al 63.2% de fallas históricas.
        "weibull_params_by_asset": {
            "_nota": "β=forma, η=vida característica (años). Calibrar con historial EPM. Overrides fallback exponencial.",
            "transformador_potencia":  {"beta": 1.31, "eta": 290}, # CIGRE/Energies 2026 Tabla 3 (>200 kV) — β≈1 casi aleatorio; η matemático, no vida física
            "interruptor":             {"beta": 3.0,  "eta": 35},  # IEEE C37 — desgaste mecánico
            "cable_mt":                {"beta": 2.2,  "eta": 30},  # β/η de referencia XLPE — calibrar con historial EPM
            "cable_at":                {"beta": 2.0,  "eta": 35},  # β/η de referencia cable AT — calibrar con historial EPM
            "tuberia_conduccion":      {"beta": 1.8,  "eta": 50},  # AWWA — corrosión gradual
            "tuberia_presion":         {"beta": 2.0,  "eta": 45},  # AWWA — ciclos presión
            "bomba_centrifuga":        {"beta": 3.5,  "eta": 20},  # FMEA — cavitación/desgaste
            "valvula_control":         {"beta": 2.8,  "eta": 25},  # ISA — asiento/vástago
            "scada_sistema":           {"beta": 4.0,  "eta": 12},  # Gartner — obsolescencia
            "reconectador":            {"beta": 2.8,  "eta": 30},  # IEEE C37 — operaciones
            "gasoducto":               {"beta": 1.6,  "eta": 55},  # ASME B31.8 — corrosión ext
            "penstock":                {"beta": 2.0,  "eta": 60},  # ASCE — fatiga hidráulica
        },
    },
    # ── Carbono Neutralidad — Factores GWP y emisión (Definición oficial EPM) ──
    # Fuente GWP: IDEAM AR4 (usado por Colombia en inventario GEI).
    # Fuente SIN: UPME Factor de Emisión Red Eléctrica Colombiana 2023.
    # Actualizar anualmente: factor SIN decrece con penetración renovable.
    "carbon_factors": {
        "_nota": "GWP AR4-IDEAM. Factor SIN UPME 2023. Actualizar anualmente.",
        "gwp": {
            "CO2":      1,
            "CH4":      25,       # Metano — fugas Gas, digestores AyA
            "N2O":      298,      # Óxido nitroso — PTAR AyA, procesos biológicos
            "SF6":      23500,    # Hexafluoruro azufre — interruptores TyD (crítico)
            "HFC_R134a":1430,     # Refrigerante R-134a — climatización subestaciones
            "HFC_R410A":2088,     # Refrigerante R-410A — equipos modernos
            "HFC_R22":  1810,     # Refrigerante R-22 — equipos legacy
        },
        # Factor de emisión red eléctrica colombiana (SIN)
        # Usado para Scope 2: pérdidas T&D y consumo eléctrico propio
        "sin_factor_tco2_per_mwh": 0.126,
        "_sin_fuente": "UPME 2023 — Factor de Emisión Red Eléctrica Colombiana",
        # Precio carbono base (actualizar con Ley 1931/2018 vigente)
        "precio_carbono_cop_tco2eq": 88_000,
        "_precio_fuente": "Costo abatimiento CORNARE 2020. Escalar ~5%/año por decreto.",
    },
    "proportionality_thresholds_COP": {
        "full_model": None,
        "simplified_model": None,
        "rule_based": None
    },
    "risk_matrix": {
        "_calibracion": (
            "Recalibración por marco de valor por negocio. "
            "Metodología específica por tipo×BU: ENS/CREG (TyD), ENFICC/capital markets (Gen), "
            "salud pública/concesión (AyA), franquicia/explosión (Gas). "
            "VSL Colombia 2024: 4,500 MM COP/vida (DNP/MinHacienda). "
            "Corrige: H&S TyD L3=0, Ambiental TyD L1=L2, Regulatorio AyA L4=L5, "
            "Regulatorio Gen L5=0, Reputacional TyD anclado a Hidroituango (evento de Gen). "
            "Fuentes: Ley 1333/2009, OR 006/2015 CREG, Ley 142/1994, Ley 1333/2009 ANLA."
        ),
        # ── Matriz multi-tipo (Copperleaf C55): 5 tipos × 5 niveles × 4 BU ──────────────
        # Metodología de monetización diferenciada por tipo y por negocio.
        # Los valores NO deben sumarse a un total genérico fijo — cada BU tiene su escala.
        # Tipos: Financiero, H&S, Ambiental, Regulatorio, Reputacional
        "consequence_types_matrix": {
            # ── FINANCIERO ────────────────────────────────────────────────────────────────
            # TyD:  ENS(MWh) × 3,500 COP/kWh + reparación emergencia + penalidad CREG
            # Gen:  MWh_perdidos × precio_spot + penalidad_ENFICC + costos_emergencia
            # AyA:  usuarios × días × costo_emergencia/usuario/día + reparación
            # Gas:  reparación infraestructura + responsabilidad civil a terceros
            "financiero": {
                "label": "Impacto Financiero", "icon": "💰",
                "description": (
                    "Pérdida económica directa: ENS monetizada (TyD/Gen), operación de emergencia "
                    "(AyA), reparación y responsabilidad civil (Gas). Excluye costos de reposición "
                    "de activos (capturados como CAPEX de intervención)."),
                "levels_by_bu": {
                    "TyD": {
                        "1": {"value_COP":    30_000_000,
                              "anchor": "Reparación equipo secundario. ENS < 1 MWh. Sin penalidad CREG."},
                        "2": {"value_COP":   200_000_000,
                              "anchor": "Falla alimentador. ~5 MWh ENS × 3,500 COP/kWh + reparación 180M."},
                        "3": {"value_COP": 1_500_000_000,
                              "anchor": "Falla transformador red. ~120 MWh ENS + reparación + overhead CREG."},
                        "4": {"value_COP": 7_000_000_000,
                              "anchor": "Falla subestación. ~1,000 MWh ENS × 3,500 + daño activo + penalidad CREG."},
                        "5": {"value_COP": 22_000_000_000,
                              "anchor": "Evento multisubestación. ~3,000 MWh ENS + daño múltiple infraestructura + CREG + emergencia. Sin ancla Hidroituango (evento de Generación)."},
                    },
                    "Generacion": {
                        "1": {"value_COP":    50_000_000,
                              "anchor": "Indisponibilidad <4h. Reparación menor. Sin penalidad ENFICC."},
                        "2": {"value_COP":   300_000_000,
                              "anchor": "Despacho perdido 1–2 días. Penalidad ENFICC menor + reparación componente."},
                        "3": {"value_COP": 1_500_000_000,
                              "anchor": "Indisponibilidad 1–4 semanas. Lucro cesante spot + penalidad ENFICC + reparación mayor."},
                        "4": {"value_COP": 8_000_000_000,
                              "anchor": "Daño catastrófico activo generación. Indisponibilidad 6–12 meses. Penalidad ENFICC + pérdida ingresos spot."},
                        "5": {"value_COP": 25_000_000_000,
                              "anchor": "Destrucción planta o presa. Multi-año sin generación. Pérdida total ENFICC + costos emergencia reconstrucción. Ref. escenario Ituango."},
                    },
                    "AyA": {
                        "1": {"value_COP":    30_000_000,
                              "anchor": "Fuga menor contenida. Reparación rápida. Sin afectación operativa."},
                        "2": {"value_COP":   120_000_000,
                              "anchor": "Rotura tubería secundaria. Carrotanque <1,000 usuarios × 2 días + reparación."},
                        "3": {"value_COP":   600_000_000,
                              "anchor": "Avería PTAR o red troncal. ~10,000 usuarios × 4 días × $30k/día + reparación + tratamiento alternativo."},
                        "4": {"value_COP": 4_000_000_000,
                              "anchor": "Colapso infraestructura mayor. ~50,000 usuarios × 3 días × $30k/día + reparación + emergencia."},
                        "5": {"value_COP": 12_000_000_000,
                              "anchor": "Colapso sistema hídrico regional. Semanas sin servicio. Operación de emergencia masiva + reparación sistémica."},
                    },
                    "Gas": {
                        "1": {"value_COP":    25_000_000,
                              "anchor": "Fuga menor contenida. Reparación < $25M. Sin evacuación."},
                        "2": {"value_COP":   100_000_000,
                              "anchor": "Rotura tubería. Corte sector. Reparación urgente + atención usuarios."},
                        "3": {"value_COP":   400_000_000,
                              "anchor": "Fuga significativa + reparación compleja. Daño menor a terceros si hay explosión."},
                        "4": {"value_COP": 2_000_000_000,
                              "anchor": "Explosión con daño a infraestructura propia + responsabilidad civil colectiva."},
                        "5": {"value_COP": 8_000_000_000,
                              "anchor": "Desastre en red distribución. Destrucción múltiples sectores. Responsabilidad civil masiva + reconstrucción."},
                    },
                },
            },
            # ── H&S ──────────────────────────────────────────────────────────────────────
            # Metodología común: VSL Colombia 2024 = 4,500 MM COP/vida (DNP/MinHacienda).
            # Costo neto EPM post-ARL: defensa legal + sentencia civil + proceso penal.
            # Diferenciación por BU: mecanismo de daño y escala de afectación.
            # TyD:  Trauma — electrocución trabajador o tercero. Escala: 1–3 víctimas.
            # Gen:  Accidente industrial — confinado/mecánico/hidrostático. Trabajadores.
            # AyA:  Salud pública masiva — epidemia hídrica (EDA, hepatitis A). Escala: cientos.
            # Gas:  Explosión residencial — afecta trabajadores + comunidad simultáneamente.
            "hs": {
                "label": "Salud y Seguridad", "icon": "⚠️",
                "description": (
                    "VSL Colombia 2024: ~4,500 MM COP/vida (DNP). Neto EPM post-ARL. "
                    "TyD/Gen: trauma laboral/electrocución. "
                    "AyA: epidemia hídrica (escala masiva, no accidente laboral). "
                    "Gas: explosión residencial simultánea trabajadores + comunidad."),
                "levels_by_bu": {
                    "TyD": {
                        "1": {"value_COP":           0,
                              "anchor": "Sin lesiones. Personal alejado de zona de riesgo."},
                        "2": {"value_COP":  30_000_000,
                              "anchor": "Lesión leve. Primeros auxilios. Sin incapacidad laboral. ARL cubre. Brecha legal EPM ~30M."},
                        "3": {"value_COP": 150_000_000,
                              "anchor": "Lesión grave con hospitalización — arc flash, caída en subestación, línea caída sobre tercero. "
                                        "ARL cubre médico; EPM: defensa legal 50M + indemnización civil 100M = 150M. "
                                        "Eliminado el valor cero anterior (indefendible para TyD)."},
                        "4": {"value_COP": 2_000_000_000,
                              "anchor": "Electrocución grave o atropello por línea caída. Incapacidad permanente o proceso penal. "
                                        "Neto EPM: legal 300M + civil 700M + proceso penal 1,000M = 2,000M."},
                        "5": {"value_COP": 9_000_000_000,
                              "anchor": "1–2 fatalidades. VSL 4,500M × 2 vidas = 9,000M neto. Proceso penal + compensación familiar + litigios."},
                    },
                    "Generacion": {
                        "1": {"value_COP":           0,
                              "anchor": "Sin lesiones. Personal operativo fuera de zona de riesgo."},
                        "2": {"value_COP":  50_000_000,
                              "anchor": "Lesión leve en planta. Atención médica. Sin incapacidad permanente. ARL cubre."},
                        "3": {"value_COP": 400_000_000,
                              "anchor": "Lesión grave / hospitalización. Accidente industrial (confinado, mecánico). "
                                        "Investigación COPASST. Defensa legal + indemnización."},
                        "4": {"value_COP": 3_500_000_000,
                              "anchor": "Fatalidad individual. VSL 4,500M neto ~3,500M después de ARL + proceso penal + compensación."},
                        "5": {"value_COP": 15_000_000_000,
                              "anchor": "3+ fatalidades. Escenario presa / derrumbe / explosión industrial. "
                                        "VSL × 3 + proceso penal masivo + costos del proceso = 15,000M."},
                    },
                    "AyA": {
                        # H&S en AyA = salud pública masiva, NO accidente laboral.
                        # Metodología: afectados × costo/caso + fatalidades × VSL + litigios
                        "1": {"value_COP":           0,
                              "anchor": "Sin riesgo de salud pública identificado."},
                        "2": {"value_COP":  50_000_000,
                              "anchor": "Exposición menor a agua sin tratar. <100 casos gastroenteritis leve. "
                                        "Atención ambulatoria. Sin hospitalizaciones."},
                        "3": {"value_COP": 800_000_000,
                              "anchor": "Brote localizado. 500–1,000 afectados con EDA/vómitos. "
                                        "~50 hospitalizaciones × 10M/caso + productividad + costos legales = 800M. "
                                        "Intervención secretaría de salud municipal."},
                        "4": {"value_COP": 8_000_000_000,
                              "anchor": "Brote significativo. 5,000 afectados, 200 hospitalizaciones, ~3 muertes. "
                                        "3 × VSL 2,500M neto = 7,500M + atención masiva + emergencia MINSALUD = 8,000M."},
                        "5": {"value_COP": 40_000_000_000,
                              "anchor": "Epidemia regional. 10+ muertes × VSL 4,500M = 45,000M + miles de enfermos + "
                                        "demandas masivas + emergencia estatal + intervención MINSALUD. "
                                        "Escala completamente distinta a accidente laboral. Ref: brotes hídricos en Colombia (Quibdó, Chocó)."},
                    },
                    "Gas": {
                        # Gas: explosión afecta SIMULTÁNEAMENTE trabajadores y comunidad residencial.
                        "1": {"value_COP":           0,
                              "anchor": "Sin lesiones. Zona evacuada preventivamente sin incidentes."},
                        "2": {"value_COP":  50_000_000,
                              "anchor": "Intoxicación leve por fuga. Atención médica ambulatoria. Sin hospitalización."},
                        "3": {"value_COP": 600_000_000,
                              "anchor": "Explosión menor. Múltiples heridos de comunidad + trabajadores. "
                                        "Hospitalizaciones. Proceso legal colectivo."},
                        "4": {"value_COP": 5_000_000_000,
                              "anchor": "Fatalidades por explosión residencial. 1–2 muertes comunidad + heridos graves. "
                                        "VSL × 2 + proceso penal + demandas colectivas. "
                                        "Ref: explosión gas Ciudad Bolívar Bogotá 2021."},
                        "5": {"value_COP": 20_000_000_000,
                              "anchor": "Desastre explosión masiva. 5+ fatalidades residenciales. "
                                        "VSL × 5 = 22,500M + heridos + litigios colectivos + defensa penal masiva. "
                                        "Colapso red residencial de un barrio completo."},
                    },
                },
            },
            # ── AMBIENTAL ────────────────────────────────────────────────────────────────
            # Metodología: remediación + multa ANLA (ente ambiental único para todos los BU).
            # TyD:  Aceite dieléctrico / PCB. Localizados. ANLA si afecta suelo/agua.
            # Gen:  Cuenca hídrica. Escala regional. ANLA + MinAmbiente (licencia ambiental).
            # AyA:  Fuente captación o cuerpo receptor. Triple regulador: ANLA + CRA + MINSALUD.
            # Gas:  Emisiones CH₄ (GWP 25x CO₂) + suelo en explosiones. ANLA + MinAmbiente.
            # NOTA: MinMinas NO es ente ambiental — sanciones técnicas/comerciales, no ambientales.
            "ambiental": {
                "label": "Impacto Ambiental", "icon": "🌿",
                "description": (
                    "Remediación + multa ANLA (Ley 1333/2009, hasta 5,000 SMMLV/día). "
                    "TyD: aceite dieléctrico/PCB. Gen: cuenca hídrica (escala regional). "
                    "AyA: fuente captación o cuerpo receptor (ANLA+CRA+MINSALUD). "
                    "Gas: CH₄ atmosférico + suelo (ANLA/MinAmbiente, no MinMinas)."),
                "levels_by_bu": {
                    "TyD": {
                        "1": {"value_COP":    10_000_000,
                              "anchor": "Derrame menor aceite dieléctrico <50L. Contenido en subestación. Limpieza. Sin reporte ANLA."},
                        "2": {"value_COP":    60_000_000,
                              "anchor": "Derrame 50–200L. Afecta suelo fuera de patio. Reporte ANLA obligatorio. Remediación básica 40M + gestión 20M. "
                                        "Corrige valor idéntico a L1 de calibración anterior."},
                        "3": {"value_COP":   300_000_000,
                              "anchor": "Derrame aceite a drenaje o fuente hídrica. Multa ANLA + remediación extensa. "
                                        "Riesgo PCB en transformadores pre-2000."},
                        "4": {"value_COP": 1_000_000_000,
                              "anchor": "Derrame PCB o aceite mayor a fuente hídrica. Investigación formal ANLA. "
                                        "Remediación especializada 500M + multa ANLA 500M."},
                        "5": {"value_COP": 3_000_000_000,
                              "anchor": "Evento ambiental mayor: PCB en acuífero o spill masivo en zona protegida. "
                                        "Remediación multi-año + sanción ANLA escala máxima. "
                                        "Corrige valor cero anterior (TyD sí tiene riesgo ambiental real por PCB)."},
                    },
                    "Generacion": {
                        "1": {"value_COP":    20_000_000,
                              "anchor": "Impacto ambiental operativo menor reportable. Toda hidro tiene algún impacto "
                                        "(sedimentos, caudal ecológico). Reporte ANLA. Corrige valor cero anterior."},
                        "2": {"value_COP":   100_000_000,
                              "anchor": "Emisión o vertimiento menor no conforme. Reporte + plan de ajuste operativo a ANLA."},
                        "3": {"value_COP":   600_000_000,
                              "anchor": "Vertimiento significativo a fuente hídrica. Afectación a fauna acuática. "
                                        "Multa ANLA + plan de manejo ambiental exigido."},
                        "4": {"value_COP": 4_000_000_000,
                              "anchor": "Daño grave a ecosistema hídrico de cuenca. Investigación ANLA. "
                                        "Remediación multi-año + riesgo de condicionamiento de licencia ambiental."},
                        "5": {"value_COP": 10_000_000_000,
                              "anchor": "Catástrofe ambiental de cuenca: sedimentos / lodos / vertimiento masivo. "
                                        "Daño irreversible a ecosistema protegido. Suspensión licencia ambiental. "
                                        "Ref: lodos Hidroituango 2018."},
                    },
                    "AyA": {
                        # Triple regulador: ANLA (ecosistema) + CRA (concesión agua) + MINSALUD (salud pública)
                        "1": {"value_COP":    10_000_000,
                              "anchor": "Fuga menor contenida. Sin afectación a fuente hídrica. Reporte interno."},
                        "2": {"value_COP":    40_000_000,
                              "anchor": "Vertimiento menor aguas residuales. Afectación puntual cuerpo receptor. Reporte CRA."},
                        "3": {"value_COP":   400_000_000,
                              "anchor": "Vertimiento significativo a fuente hídrica. Afectación acuática documentada. "
                                        "Multa CRA + investigación ANLA + plan de manejo."},
                        "4": {"value_COP": 3_000_000_000,
                              "anchor": "Contaminación de acuífero o río con afectación de usuarios. "
                                        "Triple regulador activo: ANLA + CRA + MINSALUD. Remediación extensa."},
                        "5": {"value_COP": 10_000_000_000,
                              "anchor": "Contaminación masiva fuente hídrica regional. Daño irreversible al ecosistema. "
                                        "Suspensión de concesión de captación por ANLA. Intervención estatal."},
                    },
                    "Gas": {
                        # ANLA/MinAmbiente sanciona lo ambiental. MinMinas sanciona lo técnico/comercial.
                        "1": {"value_COP":    15_000_000,
                              "anchor": "Fuga mínima contenida. Emisión CH₄ insignificante. Sin reporte obligatorio."},
                        "2": {"value_COP":    60_000_000,
                              "anchor": "Fuga a atmósfera. Emisión GEI (CH₄) no conforme. Reporte ANLA/MinAmbiente. "
                                        "Corrige: ente ambiental es ANLA, no MinMinas."},
                        "3": {"value_COP":   300_000_000,
                              "anchor": "Fuga significativa con afectación a comunidad. Multa ANLA + plan de mitigación GEI. "
                                        "Corrige: sanción ambiental es de ANLA, no de MinMinas."},
                        "4": {"value_COP": 1_500_000_000,
                              "anchor": "Incendio / explosión con impacto ambiental (suelo + aire). "
                                        "Remediación suelo contaminado + multa grave ANLA."},
                        "5": {"value_COP": 4_000_000_000,
                              "anchor": "Desastre ambiental: fuga / explosión masiva. Suelo extensamente contaminado. "
                                        "Suspensión de licencia ANLA. Remediación masiva multi-año."},
                    },
                },
            },
            # ── REGULATORIO ──────────────────────────────────────────────────────────────
            # Cada BU tiene un ente y un mecanismo de riesgo radicalmente distintos:
            # TyD:  CREG (OR 006/2015). Fórmula de sanción explícita. Riesgo = multa + interventoría.
            # Gen:  XM/CREG (ENFICC) + ANLA (licencia). Riesgo real = pérdida registro de planta.
            # AyA:  CRA + SSPD (Ley 142). Capacidad sancionatoria baja (~390M/día max).
            #       Riesgo real = revocación de CONCESIÓN (VPN de ingresos futuros).
            # Gas:  MinMinas (técnico/comercial). Riesgo real = revocación FRANQUICIA distribución.
            "regulatorio": {
                "label": "Incumplimiento Regulatorio", "icon": "⚖️",
                "description": (
                    "TyD: CREG OR 006/2015 — fórmula sanción por SAIDI/SAIFI. "
                    "Gen: XM/CREG ENFICC + pérdida registro de planta. "
                    "AyA: CRA+SSPD Ley 142 (capacidad sanción baja) — riesgo real = revocación concesión. "
                    "Gas: MinMinas técnico — riesgo real = revocación franquicia distribución."),
                "levels_by_bu": {
                    "TyD": {
                        "1": {"value_COP":     5_000_000,
                              "anchor": "Incumplimiento menor indicador SAIDI/SAIFI. Requerimiento de informe a CREG. Sin sanción económica."},
                        "2": {"value_COP":    50_000_000,
                              "anchor": "Multa CREG por violación indicador de calidad. Sanción fórmula OR 006. Sin suspensión."},
                        "3": {"value_COP": 1_000_000_000,
                              "anchor": "Multa CREG significativa OR 006/2015. Investigación formal. Plan de Mejora obligatorio + auditoría."},
                        "4": {"value_COP": 5_000_000_000,
                              "anchor": "Sanción grave CREG. Interventoría externa. Multa escala mayor + seguimiento intensivo regulatorio."},
                        "5": {"value_COP": 15_000_000_000,
                              "anchor": "Sanción máxima CREG (~100,000 SMMLV = 130,000M teórico). "
                                        "Riesgo de revocación de habilitación. Intervención SSPD. Valor realista: 15,000M."},
                    },
                    "Generacion": {
                        "1": {"value_COP":    10_000_000,
                              "anchor": "Incumplimiento menor de despacho. Sin penalidad contractual directa."},
                        "2": {"value_COP":   100_000_000,
                              "anchor": "Penalidad ENFICC por 1 día de incumplimiento. Requerimiento XM/CREG."},
                        "3": {"value_COP": 1_000_000_000,
                              "anchor": "Sanción CREG formal + penalidad contractual ENFICC por semana. Investigación formal XM."},
                        "4": {"value_COP": 5_000_000_000,
                              "anchor": "Suspensión temporal registro de planta + penalidad ENFICC completa del período suspendido."},
                        "5": {"value_COP": 12_000_000_000,
                              "anchor": "Pérdida permanente del registro de planta ante CREG O revocación licencia ambiental ANLA. "
                                        "= pérdida de todos los ingresos ENFICC futuros de esa planta. "
                                        "Para planta 300MW × 10 años: valor muy superior. 12,000M es piso conservador. "
                                        "Corrige el valor cero anterior (no tiene justificación válida)."},
                    },
                    "AyA": {
                        # CRA tiene capacidad sancionatoria MUY baja (max ~390M/día Ley 142).
                        # El riesgo real L4-L5 es la CONCESIÓN, no la multa.
                        "1": {"value_COP":     5_000_000,
                              "anchor": "Incumplimiento indicador calidad CRA. Reporte formal a SSPD."},
                        "2": {"value_COP":    30_000_000,
                              "anchor": "Multa CRA por indicador. Sanción real de CRA es baja (~30M típica). Plan de mejora."},
                        "3": {"value_COP":   200_000_000,
                              "anchor": "Investigación SSPD + CRA por falla continuidad. Auditoría extraordinaria + plan de acción."},
                        "4": {"value_COP": 3_000_000_000,
                              "anchor": "Medida de seguridad SSPD. Riesgo de intervención cautelar sobre la concesión. "
                                        "Multa máxima CRA + costos de cumplimiento forzado. "
                                        "Corrige valor idéntico a L5 de calibración anterior."},
                        "5": {"value_COP": 20_000_000_000,
                              "anchor": "Intervención SSPD con riesgo de reversión de concesión al municipio (Ley 142). "
                                        "El riesgo real NO es la multa CRA (baja) — es la pérdida de la concesión. "
                                        "VPN parcial de ingresos futuros en riesgo. 20,000M es piso conservador. "
                                        "Corrige el valor idéntico a L4 de calibración anterior."},
                    },
                    "Gas": {
                        "1": {"value_COP":     5_000_000,
                              "anchor": "Incumplimiento técnico menor. Requerimiento MinMinas. Sin sanción económica."},
                        "2": {"value_COP":    30_000_000,
                              "anchor": "Multa MinMinas menor por incumplimiento norma técnica. Plan de seguridad exigido."},
                        "3": {"value_COP":   200_000_000,
                              "anchor": "Multa significativa MinMinas por incumplimiento norma técnica + auditoría técnica obligatoria. "
                                        "Nota: sanciones ambientales son de ANLA, no de MinMinas."},
                        "4": {"value_COP": 1_000_000_000,
                              "anchor": "Suspensión temporal de franquicia de distribución en municipio X. "
                                        "Plan de acción obligatorio + auditoría técnica MinMinas."},
                        "5": {"value_COP": 8_000_000_000,
                              "anchor": "Revocación de franquicia de distribución = pérdida VPN ingresos futuros del área. "
                                        "Una franquicia en ciudad mediana genera ~30B/año. VPN 10 años al 10% WACC > 184B. "
                                        "8,000M es piso para evento de revocación parcial + transición. "
                                        "Corrige 1,000M anterior (insuficiente para capturar riesgo de franquicia)."},
                    },
                },
            },
            # ── REPUTACIONAL ─────────────────────────────────────────────────────────────
            # Canal financiero específico por BU (no NPS genérico):
            # TyD:  Riesgo tarifario CREG (presión en revisión quinquenal) + costos gestión crisis.
            # Gen:  ΔSpread crediticio × deuda vigente × TRM × duración efecto (capital markets).
            # AyA:  Presión tarifaria CRA + riesgo municipalización + relación con Alcaldía.
            # Gas:  Responsabilidad civil percibida + presión sobre franquicias municipales.
            # Hidroituango anclado SOLO a Generación (es un evento de Gen, no de TyD/AyA/Gas).
            "reputacional": {
                "label": "Impacto Reputacional", "icon": "📢",
                "description": (
                    "Canal financiero específico por BU. "
                    "TyD: riesgo tarifa CREG en revisión quinquenal. "
                    "Gen: ΔSpread crediticio × deuda USD (cuantificable — Hidroituango demostró +150bps). "
                    "AyA: presión tarifaria CRA + riesgo municipalización concesión. "
                    "Gas: impacto en franquicias municipales + responsabilidad civil percibida."),
                "levels_by_bu": {
                    "TyD": {
                        "1": {"value_COP":    20_000_000,
                              "anchor": "Quejas usuarios en redes sociales. Gestión interna. Sin cobertura mediática. Costo: equipo comunicaciones."},
                        "2": {"value_COP":   100_000_000,
                              "anchor": "Cobertura prensa local. Equipo PR activado. Pronunciamiento de Alcaldía o Gobernación. Costos directos gestión crisis."},
                        "3": {"value_COP":   500_000_000,
                              "anchor": "Cobertura nacional. Escrutinio regulatorio público. Costos directos crisis + riesgo de condicionamiento en siguiente revisión tarifaria CREG."},
                        "4": {"value_COP": 2_000_000_000,
                              "anchor": "Crisis pública regional. Presión en revisión tarifaria CREG: reducción 0.5% cargo distribución × ingresos TyD × VPN quinquenal ~1,500M + costos directos 500M."},
                        "5": {"value_COP": 5_000_000_000,
                              "anchor": "Crisis sistémica TyD. Revisión tarifaria adversa CREG: reducción 1% × ingresos × VPN 5 años ~3,500M + pérdida parcial área de servicio + costos directos. "
                                        "Sin ancla Hidroituango (es evento de Generación, no de TyD)."},
                    },
                    "Generacion": {
                        # Canal: costo de capital (ΔSpread × Deuda USD × TRM × duración)
                        # Post-Hidroituango: EPM degradada S&P/Fitch, spread amplió ~150bps sobre deuda en dólares.
                        "1": {"value_COP":    30_000_000,
                              "anchor": "Incidente interno. Comunicado mínimo. Sin impacto en calificación o imagen externa."},
                        "2": {"value_COP":   150_000_000,
                              "anchor": "Mención en medios locales. Sin impacto en rating crediticio. Costos de comunicación de crisis."},
                        "3": {"value_COP": 1_500_000_000,
                              "anchor": "Cobertura nacional + outlook negativo calificadora: +30bps × USD 500M × 4,200 × 1 año = 630M + costos directos 500M ≈ 1,500M."},
                        "4": {"value_COP": 8_000_000_000,
                              "anchor": "Downgrade 1 escalón + pérdida acceso bonos verdes: +80bps × USD 1B × 4,200 × 3 años ≈ 10,000M. "
                                        "Comparecencia ante Congreso. Valor piso: 8,000M."},
                        "5": {"value_COP": 20_000_000_000,
                              "anchor": "Hidroituango-equivalent: downgrade 2 escalones + pérdida ESG rating + acceso restringido a mercados de capital. "
                                        "+150bps × USD 2B × 4,200 × 3 años ≈ 37,800M. Valor conservador: 20,000M. "
                                        "Hidroituango es el ancla correcta SOLO para Generación."},
                    },
                    "AyA": {
                        # Canal: presión tarifaria CRA + riesgo municipalización
                        "1": {"value_COP":    15_000_000,
                              "anchor": "Quejas usuarios por corte puntual. Sin cobertura mediática relevante."},
                        "2": {"value_COP":    80_000_000,
                              "anchor": "Cobertura local. Quejas masivas. Pronunciamiento de la Alcaldía. Activación equipo PR + comunicados."},
                        "3": {"value_COP":   400_000_000,
                              "anchor": "Cobertura nacional. Crisis de servicio público. Presión política para congelar ajuste tarifario CRA."},
                        "4": {"value_COP": 2_500_000_000,
                              "anchor": "Emergencia sanitaria prolongada. Presión para congelar ajuste tarifario CRA 1 año: "
                                        "pérdida ~100B en ingresos diferidos × factor riesgo + costos directos crisis = 2,500M."},
                        "5": {"value_COP": 8_000_000_000,
                              "anchor": "Crisis con intervención estatal. Riesgo de municipalización del servicio. "
                                        "Costos directos masivos + pérdida confianza institucional prolongada + "
                                        "riesgo sobre renovación de concesión. Canal distinto a TyD y Gen."},
                    },
                    "Gas": {
                        # Canal: responsabilidad civil percibida + presión sobre franquicias
                        "1": {"value_COP":    10_000_000,
                              "anchor": "Corte puntual. Quejas usuarios. Sin cobertura mediática notable."},
                        "2": {"value_COP":    40_000_000,
                              "anchor": "Mención redes y prensa local. Equipo de atención al cliente activado."},
                        "3": {"value_COP":   300_000_000,
                              "anchor": "Incidente con cobertura nacional. Cuestionamiento seguridad operativa. "
                                        "Presión sobre franquicias en municipios vecinos."},
                        "4": {"value_COP": 2_000_000_000,
                              "anchor": "Explosión con cobertura nacional sostenida. Crisis imagen de seguridad. "
                                        "Presión en renovación de franquicias municipales + costos directos crisis."},
                        "5": {"value_COP": 6_000_000_000,
                              "anchor": "Desastre con múltiples víctimas residenciales. Cobertura internacional. "
                                        "Crisis institucional permanente. Riesgo pérdida múltiples franquicias municipales. "
                                        "Referencia: explosiones gas residencial masivas en otros países."},
                    },
                },
            },
        },
        # ── Tabla genérica de consecuencias (fallback cuando BU no se especifica) ───────
        # Valores = suma aproximada de los 5 tipos para TyD (BU más representativo).
        # Actualizada para reflejar la recalibración.
        "consequence_levels": {
            "1": {
                "label": "Muy Menor", "value_COP": 65_000_000,
                "_componentes": {"financiero": 30_000_000, "hs": 0, "ambiental": 10_000_000,
                                 "regulatorio": 5_000_000, "reputacional": 20_000_000},
                "_ancla": "Falla sin afectacion perceptible. Reparacion menor. Sin cobertura mediatica.",
            },
            "2": {
                "label": "Menor", "value_COP": 440_000_000,
                "_componentes": {"financiero": 200_000_000, "hs": 30_000_000, "ambiental": 60_000_000,
                                 "regulatorio": 50_000_000, "reputacional": 100_000_000},
                "_ancla": "Afectacion localizada. Lesion leve. Reporte ANLA. Multa CREG menor.",
            },
            "3": {
                "label": "Moderado", "value_COP": 3_450_000_000,
                "_componentes": {"financiero": 1_500_000_000, "hs": 150_000_000, "ambiental": 300_000_000,
                                 "regulatorio": 1_000_000_000, "reputacional": 500_000_000},
                "_ancla": "Afectacion regional. Lesion grave. Derrame a fuente hidrica. Multa CREG formal.",
            },
            "4": {
                "label": "Mayor", "value_COP": 17_000_000_000,
                "_componentes": {"financiero": 7_000_000_000, "hs": 2_000_000_000, "ambiental": 1_000_000_000,
                                 "regulatorio": 5_000_000_000, "reputacional": 2_000_000_000},
                "_ancla": "Impacto severo. Fatalidad. Dano ambiental mayor. Sancion grave CREG. Crisis publica.",
            },
            "5": {
                "label": "Catastrofico", "value_COP": 54_000_000_000,
                "_componentes": {"financiero": 22_000_000_000, "hs": 9_000_000_000, "ambiental": 3_000_000_000,
                                 "regulatorio": 15_000_000_000, "reputacional": 5_000_000_000},
                "_ancla": "Falla sistemica. Multiples fatalidades. Sancion maxima CREG. Crisis institucional TyD.",
            }
        },
        "probability_levels": {
            "1": {"label": "Rara",        "annual_prob": 0.002, "_ancla": "Activo optimo — vida util >80%"},
            "2": {"label": "Improbable",  "annual_prob": 0.01,  "_ancla": "Buen estado — mantenimiento al dia"},
            "3": {"label": "Posible",     "annual_prob": 0.05,  "_ancla": "Senales de deterioro — inspeccion reciente"},
            "4": {"label": "Probable",    "annual_prob": 0.20,  "_ancla": "Degradado — fin de vida util proximo"},
            "5": {"label": "Casi Cierta", "annual_prob": 0.70,  "_ancla": "Critico — falla inminente"}
        },
        # ── Umbrales de mapeo PoF acumulada Weibull → nivel de probabilidad ────────────
        # Calibrados para F(t) = 1 − exp(−(t/η)^β), valor acumulado al momento de evaluación.
        # NO usar annual_prob directamente: F(t) es acumulada, no tasa anual.
        # Correspondencia orientativa:
        #   P1 (Rara):       F(t) < 5%   → activo en vida útil temprana/media, degradación nula
        #   P2 (Improbable): F(t) < 20%  → activo con deterioro menor, aún dentro de vida útil
        #   P3 (Posible):    F(t) < 40%  → señales de degradación, inspección requerida
        #   P4 (Probable):   F(t) < 65%  → activo degradado, próximo a fin de vida útil
        #   P5 (Casi Cierta):F(t) ≥ 65%  → activo crítico, falla inminente
        "pof_level_thresholds": [0.05, 0.20, 0.40, 0.65],  # límites P1/P2, P2/P3, P3/P4, P4/P5
        # ── Umbrales de mapeo CoF (MM COP) → nivel de consecuencia ─────────────────────
        # Calibrados para TyD como BU de referencia. Los límites son geométricos
        # entre los totales de consequence_levels_by_bu TyD:
        #   L1=65M, L2=440M, L3=3,450M, L4=17,000M, L5=54,000M
        # C1 (Muy Menor):  CoF < 150M    → reparación menor, ENS < 1 MWh
        # C2 (Menor):      CoF < 1,000M  → falla alimentador + reparación + lesión leve
        # C3 (Moderado):   CoF < 7,000M  → falla subestación + multa + ambiental
        # C4 (Mayor):      CoF < 30,000M → falla sistémica + fatalidad + sanción CREG
        # C5 (Catastrófico):CoF ≥ 30,000M→ evento multi-subestación + crisis institucional
        "cof_level_thresholds_mm": [150, 1_000, 7_000, 30_000],  # límites C1/C2 … C4/C5 (MM COP)
        # ── Totales por BU (suma de los 5 tipos por nivel) ──────────────────────────────
        # Usados como CoF agregado cuando el modelo no tiene desglose por tipo.
        # Recalibrados para reflejar la nueva metodología por marco de valor por negocio.
        # NOTA (Reto 6 — Reputación, VD-6): value_COP de esta tabla ya NO incluye
        # componente reputacional. Reducido aplicando la misma proporción reputacional
        # por nivel que existe en config.json's risk_matrix.consequence_levels (tabla
        # plana) — esta tabla por-negocio nunca tuvo desglose propio directo/regulatorio/
        # reputacional, así que es una ESTIMACIÓN, no un dato calibrado directamente.
        # Pendiente de validar con Gestión del Riesgo EPM. Debe permanecer sincronizada
        # con risk_matrix.consequence_levels_by_bu en config.json.
        "consequence_levels_by_bu": {
            "TyD": {
                "1": {"label": "Muy Menor",    "value_COP":    44_687_500,
                      "_ancla": "Reparacion menor. ENS < 1 MWh. Sin penalidad CREG."},
                "2": {"label": "Menor",        "value_COP":   275_000_000,
                      "_ancla": "Falla alimentador. ENS ~5 MWh + reparacion + lesion leve."},
                "3": {"label": "Moderado",     "value_COP": 2_242_500_000,
                      "_ancla": "Falla subestacion menor. ENS + reparacion + multa CREG + ambiental."},
                "4": {"label": "Mayor",        "value_COP": 9_916_666_667,
                      "_ancla": "Falla subestacion mayor. ENS significativa + sancion CREG + fatalidad."},
                "5": {"label": "Catastrofico", "value_COP": 27_000_000_000,
                      "_ancla": "Evento multi-subestacion. ENS masiva + sancion maxima CREG + crisis institucional TyD."},
            },
            "Generacion": {
                "1": {"label": "Muy Menor",    "value_COP":    68_750_000,
                      "_ancla": "Indisponibilidad <4h. Sin penalidad ENFICC."},
                "2": {"label": "Menor",        "value_COP":   437_500_000,
                      "_ancla": "Despacho perdido 1-2 dias. Penalidad ENFICC + reparacion."},
                "3": {"label": "Moderado",     "value_COP": 3_250_000_000,
                      "_ancla": "Planta fuera 1-4 semanas. Lucro cesante + penalidad ENFICC + ambiental."},
                "4": {"label": "Mayor",        "value_COP": 16_625_000_000,
                      "_ancla": "Dano catastrofico activo. Indisponibilidad 6-12 meses + fatalidad + sancion CREG + spread crediticio."},
                "5": {"label": "Catastrofico", "value_COP": 41_000_000_000,
                      "_ancla": "Destruccion planta/presa. Perdida total ENFICC + multiples victimas + impacto capital markets. Ref. Hidroituango."},
            },
            "AyA": {
                "1": {"label": "Muy Menor",    "value_COP":    37_812_500,
                      "_ancla": "Fuga menor contenida. Sin afectacion a usuarios ni fuente hidrica."},
                "2": {"label": "Menor",        "value_COP":   200_000_000,
                      "_ancla": "Rotura tuberia secundaria. Carrotanque + reparacion + reporte CRA."},
                "3": {"label": "Moderado",     "value_COP": 1_560_000_000,
                      "_ancla": "Averia PTAR o red troncal. Emergencia usuarios + brote leve + multa CRA + ANLA."},
                "4": {"label": "Mayor",        "value_COP": 11_958_333_333,
                      "_ancla": "Colapso infraestructura + brote hidrico significativo + medida SSPD + crisis publica."},
                "5": {"label": "Catastrofico", "value_COP": 45_000_000_000,
                      "_ancla": "Colapso sistema hidrico regional + epidemia + riesgo concesion + intervencion estatal. "
                                "AyA L5 es el mayor de todos los BU por la escala de salud publica."},
            },
            "Gas": {
                "1": {"label": "Muy Menor",    "value_COP":    37_812_500,
                      "_ancla": "Fuga menor contenida. Sin evacuacion. Sin reporte obligatorio."},
                "2": {"label": "Menor",        "value_COP":   175_000_000,
                      "_ancla": "Corte sector. Reparacion urgente + reporte ANLA + multa MinMinas."},
                "3": {"label": "Moderado",     "value_COP": 1_170_000_000,
                      "_ancla": "Explosion menor. Heridos comunidad + ambiental + multa MinMinas + cobertura medios."},
                "4": {"label": "Mayor",        "value_COP": 6_708_333_333,
                      "_ancla": "Explosion con fatalidades residenciales + dano infraestructura + suspension franquicia + crisis imagen."},
                "5": {"label": "Catastrofico", "value_COP": 23_000_000_000,
                      "_ancla": "Desastre explosion masiva. Multiples victimas residenciales + revocacion franquicia + crisis institucional."},
            },
        },
    },
    "value_drivers": [
        {
            "id": "VD-1",
            "reto": "Calidad de los Servicios",
            "sub_criteria": [
                {
                    "id": "VD-1-A", "label": "ENS Evitada (energía no suministrada)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "MWh/año", "price_per_unit_COP": 335_000,
                    "_fuente": "XM precio bolsa media 3 años",
                    "bu_filter": ["TyD", "Generacion"]
                },
                {
                    "id": "VD-1-B", "label": "SAIDI/SAIFI (horas-cliente interrupción)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "horas-cliente/año", "price_per_unit_COP": 6_200,
                    "_fuente": "VENS XM 2024 + mix clientes OR EPM",
                    "bu_filter": ["TyD"]
                },
                {
                    "id": "VD-1-C", "label": "Continuidad suministro agua (m³)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "m3/año", "price_per_unit_COP": 3_100,
                    "_fuente": "Tarifa CRA 688/2014 + costo producción",
                    "bu_filter": ["AyA"]
                },
                {
                    "id": "VD-1-D", "label": "ICTAC (horas carrotanque evitadas)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "usuario-hora/año", "price_per_unit_COP": 22_000,
                    "_fuente": "Costo operativo AMVA 2024",
                    "bu_filter": ["AyA"]
                },
                {
                    "id": "VD-1-E", "label": "Disponibilidad hidráulica (MWh)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "MWh/año", "price_per_unit_COP": 355_000,
                    "_fuente": "XM + OEF CREG",
                    "bu_filter": ["Generacion"]
                },
                {
                    "id": "VD-1-F", "label": "Reputación EPM (NPS + CX + imagen)",
                    "type": "concept_rating", "weight": 1.0,
                    "value_function": "s_curve", "vf_inflection": 5.0, "vf_steepness": 1.5,
                    "scale_max_COP": 320_000_000_000,
                    "scale_max_by_bu": {
                        "TyD": 280_000_000_000, "AyA": 320_000_000_000,
                        "Generacion": 380_000_000_000, "Gas": 180_000_000_000,
                        "Todos": 420_000_000_000
                    },
                    "_calibracion": "20% ingreso regulado anual x factor exposicion reputacional",
                    "deprecated": True,
                    "deprecated_reason": (
                        "Contenido trasladado a VD-6-A (Reto Reputación, agregado por EPM). "
                        "Usar VD-1-F junto con VD-6-A duplicaría el aporte reputacional dentro "
                        "de los value drivers. Ya NO es sobre la matriz de riesgo — el "
                        "componente reputacional se retiró de las anclas L1-L5 al mismo tiempo "
                        "que se creó VD-6. Conservado para trazabilidad de evaluaciones "
                        "históricas anteriores a este cambio."
                    ),
                    "deprecated_since": "config_v3.0.0"
                }
            ]
        },
        {
            "id": "VD-2",
            "reto": "Servicios Eficientes",
            "sub_criteria": [
                {
                    "id": "VD-2-A", "label": "OPEX evitado (reducción costos O&M)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "COP/año", "price_per_unit_COP": 1,
                    "_fuente": "Directo: 1 COP ahorrado = 1 COP de valor"
                },
                {
                    "id": "VD-2-B", "label": "Pérdidas gas evitadas (m³)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "m3/año", "price_per_unit_COP": 2_100,
                    "_fuente": "Precio puerta + distribución",
                    "bu_filter": ["Gas"]
                },
                {
                    "id": "VD-2-C", "label": "Reducción pérdidas técnicas electricidad (MWh)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "MWh/año", "price_per_unit_COP": 335_000,
                    "_fuente": "XM precio bolsa",
                    "bu_filter": ["TyD"]
                }
            ]
        },
        {
            "id": "VD-3",
            "reto": "Cobertura Universal Sostenible",
            "sub_criteria": [
                {
                    "id": "VD-3-A", "label": "Nuevas conexiones T&D (LTV cliente)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "conexiones", "price_per_unit_COP": 5_200_000,
                    "_fuente": "Facturación × 15 años × WACC 10%",
                    "bu_filter": ["TyD"]
                },
                {
                    "id": "VD-3-B", "label": "Nuevas conexiones AyA (LTV cliente)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "conexiones", "price_per_unit_COP": 6_800_000,
                    "_fuente": "Facturación dual × 20 años × WACC 10%",
                    "bu_filter": ["AyA"]
                },
                {
                    "id": "VD-3-C", "label": "Nuevas conexiones Gas (LTV cliente)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "conexiones", "price_per_unit_COP": 4_100_000,
                    "_fuente": "Facturación × 20 años × WACC 10%",
                    "bu_filter": ["Gas"]
                }
            ]
        },
        {
            "id": "VD-4",
            "reto": "Protección Hídrica y Carbono Neutralidad",
            "sub_criteria": [
                {
                    "id": "VD-4-A", "label": "Reducción emisiones GEI (tCO₂eq)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "tCO2eq/año", "price_per_unit_COP": 88_000,
                    "_fuente": "Costo abatimiento CORNARE"
                },
                {
                    "id": "VD-4-B", "label": "Protección hídrica (ha ecosistemas)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "ha/año", "price_per_unit_COP": 4_800_000,
                    "_fuente": "Fondo Agua EPM + CORNARE",
                    "bu_filter": ["AyA", "Generacion"]
                },
                {
                    "id": "VD-4-C", "label": "Multas ambientales evitadas (eventos ANLA)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "eventos/año", "price_per_unit_COP": 3_200_000_000,
                    "_fuente": "Ley 1333/2009 + historial ANLA"
                },
                {
                    # Scope 2 Definición EPM: pérdidas de energía en sistemas T&D
                    # price = factor_SIN × precio_carbono = 0.126 tCO₂eq/MWh × $88,000
                    "id": "VD-4-D", "label": "Scope 2 — Pérdidas técnicas red T&D (MWh evitados)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "MWh/año", "price_per_unit_COP": 11_088,
                    "_fuente": "UPME 2023 (0.126 tCO₂eq/MWh) × CORNARE $88,000/tCO₂eq",
                    "_scope": "Scope 2 — pérdidas transmisión y distribución",
                    "bu_filter": ["TyD"]
                },
                {
                    # Scope 2 Definición EPM: consumo de energía eléctrica del sistema nacional
                    # price = mismo factor_SIN × precio_carbono
                    "id": "VD-4-E", "label": "Scope 2 — Consumo eléctrico propio reducido (MWh)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "MWh/año", "price_per_unit_COP": 11_088,
                    "_fuente": "UPME 2023 (0.126 tCO₂eq/MWh) × CORNARE $88,000/tCO₂eq",
                    "_scope": "Scope 2 — consumo eléctrico propio de bombas, compresores, aux. planta",
                    "bu_filter": ["AyA", "Gas", "Generacion"]
                }
            ]
        },
        {
            "id": "VD-5",
            "reto": "Generación de Valor",
            "sub_criteria": [
                {
                    "id": "VD-5-A", "label": "Ingresos habilitados / multas CREG evitadas",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "COP/año", "price_per_unit_COP": 1,
                    "_fuente": "Directo"
                },
                {
                    "id": "VD-5-B", "label": "Paradas PTAR evitadas (horas)",
                    "type": "monetizable", "weight": 1.0,
                    "unit": "horas/año", "price_per_unit_COP": 4_500_000,
                    "_fuente": "Costos by-pass + riesgo ANLA",
                    "bu_filter": ["AyA"]
                },
                {
                    "id": "VD-5-C", "label": "Rentabilidad portafolio de activos",
                    "type": "concept_rating", "weight": 1.0,
                    "value_function": "s_curve", "vf_inflection": 5.0, "vf_steepness": 1.5,
                    "scale_max_COP": 400_000_000_000,
                    "scale_max_by_bu": {
                        "TyD": 350_000_000_000, "AyA": 400_000_000_000,
                        "Generacion": 600_000_000_000, "Gas": 200_000_000_000,
                        "Todos": 1_750_000_000_000
                    },
                    "_calibracion": "Base activos por negocio × 5pp mejora máxima benchmark CREG"
                }
            ]
        },
        {
            "id": "VD-6",
            "reto": "Reputación",
            "weight": 1.0,
            "sub_criteria": [
                {
                    "id": "VD-6-A", "label": "Reputación EPM (NPS + CX + imagen)",
                    "type": "concept_rating", "weight": 1.0,
                    "value_function": "s_curve", "vf_inflection": 5.0, "vf_steepness": 1.5,
                    "scale_max_COP": 320_000_000_000,
                    "scale_max_by_bu": {
                        "TyD": 280_000_000_000, "AyA": 320_000_000_000,
                        "Generacion": 380_000_000_000, "Gas": 180_000_000_000,
                        "Todos": 420_000_000_000
                    },
                    "_fuente": "Reactivado de VD-1-F (retirado en config_v3.0.0). Calibración "
                               "original conservada sin cambios: 20% ingreso regulado anual × "
                               "factor exposición reputacional.",
                    "_nota": "Reto agregado por EPM sin definición propia — uniforme para los "
                             "4 negocios, metodología heredada de VD-1-F/Copperleaf VDM. Ver "
                             "REGISTRO_ASUNTOS_ABIERTOS.md D-11 para la distinción metodológica "
                             "frente a la fuente Copperleaf (que documenta reputación como "
                             "categoría de riesgo, no como Modelo de Valor)."
                }
            ]
        }
    ]
}

# Tipos de inversión → sub-criterios obligatorios (por ID)
INVESTMENT_TYPES = {
    "Reposición de Activos": {
        "description": "Reemplazo de activo al final de su vida útil",
        "mandatory_sc": ["VD-1-A", "VD-1-B", "VD-2-A"],
        "suggested_sc": ["VD-1-C", "VD-1-D", "VD-1-E", "VD-5-A", "VD-5-D", "VD-6-A"]
        # VD-5-D: la reposición mantiene el activo en la base de remuneración CREG;
        # si el activo se retira y reemplaza, la remuneración puede incrementarse
        # si el VRN nuevo es mayor al VRN depreciado del activo reemplazado.
    },
    "Crecimiento / Expansión": {
        "description": "Nuevas conexiones o ampliación de capacidad",
        "mandatory_sc": ["VD-3-A", "VD-3-B", "VD-3-C"],
        "suggested_sc": ["VD-1-A", "VD-1-B", "VD-5-C", "VD-5-D", "VD-6-A"]
        # VD-5-D: proyectos de expansión habilitan nuevos activos que CREG
        # reconoce a VRN → ingreso regulado anual = VRN_nuevo × tasa_CREG.
    },
    "Cumplimiento Regulatorio": {
        "description": "Inversión requerida por norma CREG/CRA/ANLA",
        "mandatory_sc": ["VD-5-A", "VD-4-C"],
        "suggested_sc": ["VD-1-B", "VD-2-A", "VD-5-D", "VD-6-A"]
        # VD-5-D: proyectos de cumplimiento frecuentemente habilitan activos
        # nuevos (reemplazos, upgrades) reconocibles en la base regulatoria CREG.
    },
    "Eficiencia Operativa": {
        "description": "Reducción de OPEX o pérdidas técnicas",
        "mandatory_sc": ["VD-2-A"],
        "suggested_sc": ["VD-2-B", "VD-2-C", "VD-1-A", "VD-5-A", "VD-6-A"]
    },
    "Sostenibilidad / ESG": {
        "description": "Reducción huella ambiental y carbono",
        "mandatory_sc": ["VD-4-A"],
        "suggested_sc": ["VD-4-B", "VD-4-C", "VD-5-C", "VD-6-A"]
    },
    "Proyecto Empresarial / TI": {
        "description": "Iniciativas tecnológicas o de transformación organizacional",
        "mandatory_sc": ["VD-5-A", "VD-5-C"],
        "suggested_sc": ["VD-2-A", "VD-3-A", "VD-6-A"]
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# BU_DRIVER_CONFIG — Segmentación de Negocios
# ══════════════════════════════════════════════════════════════════════════════
# Separación explícita de T&D y Generación: drivers, métricas y precios
# propios para cada negocio. Elimina el término ambiguo "Energía".
#
# Estructura:
#   BU_DRIVER_CONFIG[bu_key] = {
#       "label":   nombre visible en UI,
#       "focus":   descripción del foco estratégico del negocio,
#       "sub_criteria": { sc_id: {label, unit, price_COP, vd_id, ...} }
#   }
BU_DRIVER_CONFIG: Dict[str, dict] = {
    "TyD": {
        "label":  "Transmisión y Distribución (T&D)",
        "focus":  "Confiabilidad de red · ENS · SAIDI · Cobertura nuevas conexiones",
        "sub_criteria": {
            "VD-1-A": {
                "label":     "ENS evitada — Energía No Suministrada",
                "unit":      "MWh/año",
                "price_COP": 335_000,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios",
                "metodologia_sugerida": "driver_based",
                "_fuente": "XM — precio promedio bolsa 3 años",
            },
            "VD-1-B": {
                "label":     "SAIDI — horas-cliente interrupción evitadas",
                "unit":      "horas-cliente/año",
                "price_COP": 6_200,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios",
                "metodologia_sugerida": "driver_based",
                "_fuente": "VENS XM 2024 + mix clientes EPM",
            },
            "VD-2-A": {
                "label":     "OPEX evitado (ahorro en O&M)",
                "unit":      "MM COP/año",
                "price_COP": 1_000_000,      # × 1e6 por ser MM COP
                "vd_id":     "VD-2",
                "reto":      "Servicios Eficientes",
                "metodologia_sugerida": "driver_based",
            },
            "VD-3-A": {
                "label":     "Nuevas conexiones eléctricas habilitadas (LTV)",
                "unit":      "conexiones",
                "price_COP": 5_200_000,
                "vd_id":     "VD-3",
                "reto":      "Cobertura Universal Sostenible",
                "metodologia_sugerida": "driver_based",
                "_fuente": "LTV facturación 15a al WACC 10%",
            },
            "VD-4-A": {
                "label":     "CO₂ evitado (desplazamiento generación térmica)",
                "unit":      "tCO₂eq/año",
                "price_COP": 88_000,
                "vd_id":     "VD-4",
                "reto":      "Protección Hídrica y Carbono Neutralidad",
                "metodologia_sugerida": "driver_based",
            },
            "VD-5-B": {
                "label":     "Multas CREG evitadas",
                "unit":      "MM COP/año",
                "price_COP": 1_000_000,
                "vd_id":     "VD-5",
                "reto":      "Generación de Valor",
                "metodologia_sugerida": "driver_based",
            },
            "VD-5-D": {
                # ── Rate Base Enablement — Copperleaf C55 §3.2 ─────────────────
                # Ingreso regulado reconocido por CREG cuando un activo nuevo
                # entra al Sistema de Distribución Local (SDL) o STN.
                #
                # Fórmula CREG 015/2018 Art.15:
                #   Remuneración anual = VRN × (WACC_reg + tasa_depreciación)
                #
                # El analista ingresa el RESULTADO (MM COP/año) usando la
                # calculadora CREG integrada en el Wizard (VRN + clase de activo).
                # price_COP=1e6 convierte MM COP/año → COP/año interno del motor.
                #
                # Diferencia conceptual vs VD-5-B:
                #   VD-5-B = Costo evitado (multa que no se paga)
                #   VD-5-D = Ingreso real reconocido en tarifa CARGO D por activo nuevo
                "label":     "Ingresos regulados CREG — Remuneración SDL/STN",
                "unit":      "MM COP/año",
                "price_COP": 1_000_000,
                "vd_id":     "VD-5",
                "reto":      "Generación de Valor",
                "metodologia_sugerida": "driver_based",
                "_fuente":   "CREG Res.015/2018 Art.15 (SDL) · Res.011/2009 (STN)",
                "_nota":     (
                    "Ingrese el ingreso anual regulado resultado de la calculadora: "
                    "VRN × (WACC_CREG + tasa_dep). WACC_CREG=11.3% (Res.015/2018)."
                ),
                # Parámetros CREG para la calculadora integrada del Wizard
                "_creg_params": {
                    "wacc_reg":            0.113,    # WACC regulatorio CREG 015/2018 (antes de impuestos)
                    "inflation_ref":       0.040,    # Inflación referencia indexación VRN
                    "asset_classes": {
                        # Tasa de depreciación por clase de activo (CREG 015/2018 Anexo 1)
                        "Líneas subtransmisión (34.5–115 kV)":   0.020,
                        "Subestaciones (34.5–115 kV)":           0.025,
                        "Redes distribución primaria (< 34.5 kV)": 0.033,
                        "Redes distribución secundaria (< 1 kV)": 0.040,
                        "Equipos de medición":                    0.050,
                        "Líneas de transmisión STN (> 115 kV)":  0.015,
                        "Subestaciones STN (> 115 kV)":          0.020,
                    },
                },
                "bu_filter": ["TyD"],   # Solo visible para T&D
            },
            "VD-1-TR": {
                "label":     "Riesgo de falla — transformador de potencia (ENS esperada)",
                "unit":      "MWh/año",
                "price_COP": 335_000,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios",
                "metodologia_sugerida": "asset_aware",
                "asset_family": "transformador_potencia",
                "_fuente": "PoF_Weibull × ENS_consecuencia · Riesgo = PoF × Consecuencia (GM-65)",
            },
        },
    },
    "Generacion": {
        "label":  "Generación",
        "focus":  "Disponibilidad de planta · Factor de planta · Riesgo activos críticos · Ingresos contrato",
        "sub_criteria": {
            "VD-1-G": {
                "label":     "Disponibilidad de planta evitada (MWh pérdida por indisponibilidad)",
                "unit":      "MWh/año",
                "price_COP": 220_000,        # precio contrato largo plazo EPM
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios — Disponibilidad de Planta",
                "metodologia_sugerida": "asset_aware",
                "_fuente": "Precio contrato LP EPM + prima renovables 2025",
            },
            "VD-1-H": {
                "label":     "Reducción de indisponibilidad forzada (horas/año)",
                "unit":      "horas indisponibilidad/año",
                "price_COP": 180_000_000,    # costo por hora fuera de servicio (pérdida contrato)
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios — Riesgo Activos Críticos",
                "metodologia_sugerida": "asset_aware",
                "_fuente": "Costo oportunidad contrato ENFICC EPM 2025",
            },
            "VD-2-A": {
                "label":     "OPEX evitado (mantenimiento correctivo vs. preventivo)",
                "unit":      "MM COP/año",
                "price_COP": 1_000_000,
                "vd_id":     "VD-2",
                "reto":      "Servicios Eficientes",
                "metodologia_sugerida": "driver_based",
            },
            "VD-4-A": {
                "label":     "CO₂ evitado (generación limpia incremental)",
                "unit":      "tCO₂eq/año",
                "price_COP": 88_000,
                "vd_id":     "VD-4",
                "reto":      "Protección Hídrica y Carbono Neutralidad",
                "metodologia_sugerida": "driver_based",
            },
            "VD-4-B": {
                "label":     "Multas ANLA evitadas (riesgo ambiental activos hídricos)",
                "unit":      "eventos/año",
                "price_COP": 3_200_000_000,
                "vd_id":     "VD-4",
                "reto":      "Protección Hídrica y Carbono Neutralidad",
                "metodologia_sugerida": "driver_based",
                "_fuente": "Ley 1333/2009 + historial ANLA EPM",
            },
            "VD-5-A": {
                "label":     "Ingresos contrato generación habilitados (MM COP/año)",
                "unit":      "MM COP/año",
                "price_COP": 1_000_000,
                "vd_id":     "VD-5",
                "reto":      "Generación de Valor",
                "metodologia_sugerida": "driver_based",
            },
            "VD-1-TR": {
                "label":     "Riesgo de falla — transformador elevador de planta (horas indisponibilidad esperadas)",
                "unit":      "horas indisponibilidad/año",
                "price_COP": 180_000_000,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios — Riesgo Activos Críticos",
                "metodologia_sugerida": "asset_aware",
                "asset_family": "transformador_potencia",
                "_fuente": "PoF_Weibull × costo oportunidad contrato ENFICC · Riesgo = PoF × Consecuencia (GM-65)",
            },
            "VD-1-TUB": {
                "label":     "Riesgo de falla — tubería de conducción (penstock / galería) (horas indisponibilidad esperadas)",
                "unit":      "horas indisponibilidad/año",
                "price_COP": 180_000_000,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios — Disponibilidad de Planta",
                "metodologia_sugerida": "asset_aware",
                "asset_family": "tuberia_conduccion",
                "_fuente": "PoF_Weibull × pérdida generación · ASCE MOP 79 (Steel Penstocks)",
            },
        },
    },
    "AyA": {
        "label":  "Aguas y Aguas (AyA)",
        "focus":  "Continuidad del servicio hídrico · Pérdidas · IPCU · Cobertura acueducto",
        "sub_criteria": {
            "VD-1-C": {
                "label":     "Agua no suministrada evitada",
                "unit":      "m³/año",
                "price_COP": 3_100,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios",
                "metodologia_sugerida": "driver_based",
            },
            "VD-1-D": {
                "label":     "Horas carrotanque evitadas (ICTAC)",
                "unit":      "horas/año",
                "price_COP": 22_000,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios",
                "metodologia_sugerida": "driver_based",
            },
            "VD-2-A": {
                "label":     "OPEX evitado (reducción pérdidas técnicas/admin)",
                "unit":      "MM COP/año",
                "price_COP": 1_000_000,
                "vd_id":     "VD-2",
                "reto":      "Servicios Eficientes",
                "metodologia_sugerida": "driver_based",
            },
            "VD-3-B": {
                "label":     "Nuevas conexiones acueducto habilitadas (LTV)",
                "unit":      "conexiones",
                "price_COP": 6_800_000,
                "vd_id":     "VD-3",
                "reto":      "Cobertura Universal Sostenible",
                "metodologia_sugerida": "driver_based",
            },
            "VD-4-A": {
                "label":     "CO₂ evitado",
                "unit":      "tCO₂eq/año",
                "price_COP": 88_000,
                "vd_id":     "VD-4",
                "reto":      "Protección Hídrica y Carbono Neutralidad",
                "metodologia_sugerida": "driver_based",
            },
            "VD-4-B": {
                "label":     "Multas ANLA evitadas",
                "unit":      "eventos/año",
                "price_COP": 3_200_000_000,
                "vd_id":     "VD-4",
                "reto":      "Protección Hídrica y Carbono Neutralidad",
                "metodologia_sugerida": "driver_based",
            },
            "VD-1-TR": {
                "label":     "Riesgo de falla — transformador estación de bombeo (m³ agua no suministrada esperados)",
                "unit":      "m³/año",
                "price_COP": 3_100,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios",
                "metodologia_sugerida": "asset_aware",
                "asset_family": "transformador_potencia",
                "_fuente": "PoF_Weibull × agua no suministrada · Riesgo = PoF × Consecuencia (GM-65)",
            },
            "VD-1-TUB": {
                "label":     "Riesgo de falla — tubería de conducción (m³ agua no suministrada esperados)",
                "unit":      "m³/año",
                "price_COP": 3_100,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios",
                "metodologia_sugerida": "asset_aware",
                "asset_family": "tuberia_conduccion",
                "_fuente": "PoF_Weibull × pérdida caudal · AWWA C200 / ISO 4427",
            },
            # ── VD-5-E: Ingresos regulados CRA — CMI infraestructura ───────────
            # Mecanismo CRA 688/2014 + 906/2019: inversión aprobada en el Plan de
            # Inversiones (VPI) incrementa el CMI (Costo Máximo de Inversión) que
            # se cobra volumétricamente ($/m³) en la tarifa de acueducto.
            #
            # Diferencia vs VD-3-B (LTV conexiones): este driver cubre ampliaciones
            # de capacidad (PTAP, PTAR, redes de refuerzo, tanques) que NO generan
            # nuevas conexiones directas pero incrementan el VPI reconocido en tarifa.
            #
            # Restricción CRA: la inversión debe estar dentro del Plan de Inversiones
            # aprobado para el período tarifario vigente (5 años). Inversiones no
            # planificadas no generan CMI adicional automáticamente.
            #
            # El Wizard ofrece dos métodos al analista (campo _method_options):
            #   "aproximacion": ΔVPI × (WACC_CRA + tasa_dep)  [mismo patrón T&D]
            #   "directo":      ingreso MM COP/año calculado externamente (EAFI/CRA)
            "VD-5-E": {
                "label":     "Ingresos regulados CRA — CMI por inversión en infraestructura",
                "unit":      "MM COP/año",
                "price_COP": 1_000_000,
                "vd_id":     "VD-5",
                "reto":      "Generación de Valor",
                "metodologia_sugerida": "driver_based",
                "_fuente":   "CRA Res.688/2014 §57 · Res.906/2019 — CMI acueducto/alcantarillado",
                "_nota": (
                    "Método A (aproximación): ΔVPI × (WACC_CRA + tasa_dep). "
                    "Método B (directo): MM COP/año de ΔCMI × m³/año (herramienta CRA). "
                    "La inversión debe estar dentro del Plan de Inversiones CRA aprobado."
                ),
                "_method_options": ["aproximacion", "directo"],
                "_cra_params": {
                    "wacc_cra":  0.101,    # CRA 688/2014 §57, referencia prestador grande
                    "wacc_nota": (
                        "~10.1% CRA 688/2014 §57. Varía por segmento bajo CRA 906/2019. "
                        "EPM AyA como prestador grande: validar con radicación tarifaria vigente."
                    ),
                    "asset_classes": {
                        "Planta tratamiento agua potable (PTAP)":   0.025,
                        "Planta tratamiento aguas residuales (PTAR)": 0.025,
                        "Líneas de conducción / aducción":           0.020,
                        "Redes distribución primaria":               0.033,
                        "Redes distribución secundaria":             0.040,
                        "Reservorios y tanques":                     0.020,
                        "Macro/micromedición":                       0.050,
                    },
                },
                "bu_filter": ["AyA"],
            },
        },
    },
    "Gas": {
        "label":  "Gas Natural",
        "focus":  "Integridad de red · Frecuencia de fugas · Expansión cobertura gas",
        "sub_criteria": {
            "VD-1-E": {
                "label":     "Fugas de gas evitadas",
                "unit":      "eventos/año",
                "price_COP": 50_000_000,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios",
                "metodologia_sugerida": "driver_based",
            },
            "VD-2-A": {
                "label":     "OPEX evitado (O&M red, eficiencia operativa)",
                "unit":      "MM COP/año",
                "price_COP": 1_000_000,
                "vd_id":     "VD-2",
                "reto":      "Servicios Eficientes",
                "metodologia_sugerida": "driver_based",
            },
            "VD-3-C": {
                "label":     "Nuevas conexiones gas (LTV)",
                "unit":      "conexiones",
                "price_COP": 3_500_000,
                "vd_id":     "VD-3",
                "reto":      "Cobertura Universal Sostenible",
                "metodologia_sugerida": "driver_based",
            },
            "VD-4-A": {
                "label":     "CO₂ evitado (sustitución leña/carbón por gas)",
                "unit":      "tCO₂eq/año",
                "price_COP": 88_000,
                "vd_id":     "VD-4",
                "reto":      "Protección Hídrica y Carbono Neutralidad",
                "metodologia_sugerida": "driver_based",
            },
            "VD-1-TR": {
                "label":     "Riesgo de falla — transformador ERM (eventos de corte esperados)",
                "unit":      "eventos/año",
                "price_COP": 50_000_000,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios",
                "metodologia_sugerida": "asset_aware",
                "asset_family": "transformador_potencia",
                "_fuente": "PoF_Weibull × costo interrupción despacho · Riesgo = PoF × Consecuencia (GM-65)",
            },
            "VD-1-TUB": {
                "label":     "Riesgo de falla — tubería / gasoducto (eventos de fuga esperados)",
                "unit":      "eventos/año",
                "price_COP": 50_000_000,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios",
                "metodologia_sugerida": "asset_aware",
                "asset_family": "tuberia_conduccion",
                "_fuente": "PoF_Weibull × costo fuga/corte · API 580",
            },
            # ── VD-5-E: Ingresos regulados CREG gas (Cargo D) ──────────────────
            # Mecanismo: idéntico a T&D. VRN × (WACC_gas + tasa_dep) = remun. anual.
            # WACC activo: 10.94% (CREG Res.4/2021). Historial de volatilidad:
            #   CREG 102-002/2023 subió a 11.88% → Gobierno revirtió mar-2026,
            #   ordenó reembolso de ~$150 mil MM a usuarios.
            # Diferencia vs VD-3-C (LTV conexiones): este driver cubre infraestructura
            # de refuerzo/expansión que entra a la base regulatoria sin generar
            # nuevas conexiones directas (gasoductos de mayor capacidad, ERMs nuevas).
            "VD-5-E": {
                "label":     "Ingresos regulados CREG — Remuneración red gas (Cargo D)",
                "unit":      "MM COP/año",
                "price_COP": 1_000_000,
                "vd_id":     "VD-5",
                "reto":      "Generación de Valor",
                "metodologia_sugerida": "driver_based",
                "_fuente":   "CREG Res.126/2010 · Res.4/2021 (WACC 10.94%) · Res.125/2020",
                "_nota": (
                    "Remuneración anual = VRN × (WACC_gas + tasa_dep). "
                    "WACC vigente: 10.94% (CREG Res.4/2021). "
                    "Validar ante cambios regulatorios — el WACC fue disputado en 2023-2026."
                ),
                "_creg_params": {
                    "wacc_reg":   0.1094,   # CREG Res.4/2021 (activo)
                    "wacc_nota":  (
                        "10.94% CREG Res.4/2021. Historial: subió a 11.88% (Res.102-002/2023), "
                        "revertido por Gobierno en 2026 con reembolso a usuarios."
                    ),
                    "asset_classes": {
                        "Gasoductos alta presión (> 4 bar)":      0.020,
                        "Redes media presión (0.02–4 bar)":        0.025,
                        "Redes baja presión / domiciliaria":       0.033,
                        "ERM (Estación de Regulación y Medición)": 0.035,
                        "Acometidas y medidores":                  0.050,
                    },
                },
                "bu_filter": ["Gas"],
            },
        },
    },
    "Todos": {
        "label":  "Proyectos Transversales / TI",
        "focus":  "Eficiencia operativa · Transformación digital · Cumplimiento regulatorio",
        "sub_criteria": {
            "VD-2-A": {
                "label":     "OPEX evitado / ahorro operativo",
                "unit":      "MM COP/año",
                "price_COP": 1_000_000,
                "vd_id":     "VD-2",
                "reto":      "Servicios Eficientes",
                "metodologia_sugerida": "driver_based",
            },
            "VD-4-A": {
                "label":     "CO₂ evitado",
                "unit":      "tCO₂eq/año",
                "price_COP": 88_000,
                "vd_id":     "VD-4",
                "reto":      "Protección Hídrica y Carbono Neutralidad",
                "metodologia_sugerida": "driver_based",
            },
            "VD-5-A": {
                "label":     "Ingresos incrementales / multas CREG/CRA evitadas",
                "unit":      "MM COP/año",
                "price_COP": 1_000_000,
                "vd_id":     "VD-5",
                "reto":      "Generación de Valor",
                "metodologia_sugerida": "driver_based",
            },
            "VD-1-TR": {
                "label":     "Riesgo de falla — transformador corporativo / UPS (horas fuera de servicio esperadas)",
                "unit":      "horas/año",
                "price_COP": 5_000_000,
                "vd_id":     "VD-1",
                "reto":      "Calidad de los Servicios",
                "metodologia_sugerida": "asset_aware",
                "asset_family": "transformador_potencia",
                "_fuente": "PoF_Weibull × costo hora indisponibilidad TI · Riesgo = PoF × Consecuencia (GM-65)",
            },
        },
    },
}
"""Catálogo canónico de sub-criterios por Unidad de Negocio.
Exportado desde epm_core para uso directo en la interfaz Streamlit.
La segmentación T&D / Generación elimina el término 'Energía' y
asigna drivers propios a cada lógica de negocio.

Este dict es el VALOR POR DEFECTO. La fuente de verdad vive en
config["bu_driver_config"], para que se pueda calibrar sin tocar código.
apply_bu_driver_config() lo sobreescribe en sitio al cargar la configuración."""


def apply_bu_driver_config(config: dict) -> dict:
    """Sobreescribe BU_DRIVER_CONFIG con lo que traiga config['bu_driver_config'].

    El catálogo de sub-criterios por negocio vivía hardcodeado en este módulo,
    de modo que calibrarlo exigía editar código — algo que contradice el resto del
    modelo, donde todo parámetro es configurable. Ahora el config manda.

    La sobreescritura es EN SITIO (clear + update) a propósito: la app hace
    `from epm_core import BU_DRIVER_CONFIG`, así que reasignar el nombre del módulo
    no alcanzaría — la app seguiría viendo el dict viejo.

    Sin la clave en config, no hace nada y quedan los valores por defecto.
    """
    override = config.get("bu_driver_config")
    if not isinstance(override, dict):
        return BU_DRIVER_CONFIG
    limpio = {k: v for k, v in override.items()
              if not k.startswith("_") and isinstance(v, dict)}
    if not limpio:
        return BU_DRIVER_CONFIG
    BU_DRIVER_CONFIG.clear()
    BU_DRIVER_CONFIG.update(limpio)
    return BU_DRIVER_CONFIG

# ══════════════════════════════════════════════════════════════════════════════
# ASSET_FAMILIES — Familias de activos con curvas de deterioro Weibull
# ══════════════════════════════════════════════════════════════════════════════
# Cada familia define:
#   • bus_aplicables: en qué BUs puede aparecer este tipo de activo
#   • metodologia_preferred: techo de precisión (asset_aware > driver_based > concept_rating)
#   • weibull_defaults: parámetros industriales de referencia cuando el analista
#     no dispone de datos propios (β, η en años, norma de referencia)
#   • sc_ids: sub-criterios de BU_DRIVER_CONFIG que corresponden a esta familia
#   • fallback_allowed: niveles a los que puede bajar el analista (Data Maturity)
#
# Lógica Copperleaf C55 — Data Maturity Levels:
#   Nivel 3 (Calculado)  = asset_aware  → PoF_Weibull × CoF
#   Nivel 2 (Estimado)   = driver_based → Δ físico × precio regulatorio
#   Nivel 1 (Conceptual) = concept_rating → escala 0-6
# El analista solo puede BAJAR de nivel, nunca subir sin evidencia.
# La brecha entre metodologia_preferred y nivel declarado genera un
# "confidence_gap" que queda en el snapshot ISO 55001 §4.5.
ASSET_FAMILIES: Dict[str, dict] = {
    "transformador_potencia": {
        "label":   "Transformador de potencia",
        "icon":    "⚡",
        "bus_aplicables": ["TyD", "Generacion", "AyA", "Gas", "Todos"],
        "metodologia_preferred": "asset_aware",
        "fallback_allowed": ["driver_based", "concept_rating"],
        "weibull_defaults": {
            "beta": 1.31,
            "eta":  290.0,
            "norma": "CIGRE/Energies 2026 (β/η, Tabla 3 >200 kV); IEC 60076-7 (modelo de vida)",
            "nota":  "β=1.31 (distribución casi aleatoria, poco desgaste marcado por edad — "
                     "hallazgo poblacional CIGRE); η=290a es vida característica matemática de la "
                     "distribución plana, no vida física esperada. Reemplaza AusNet β=3.5/η=35.",
        },
        "modos_falla": [
            "Falla dieléctrica (degradación aislamiento papel-aceite)",
            "Sobrecalentamiento de devanados",
            "Falla de bujes o cambiador de tomas",
        ],
        "sc_ids": ["VD-1-TR"],
    },
    "interruptor_seccionador": {
        "label":   "Interruptor / seccionador AT-MT",
        "icon":    "🔌",
        "bus_aplicables": ["TyD", "Generacion"],
        "metodologia_preferred": "asset_aware",
        "fallback_allowed": ["driver_based", "concept_rating"],
        "weibull_defaults": {
            "beta": 2.8,
            "eta":  25.0,
            "norma": "IEC 62271-100",
            "nota":  "β=2.8 (desgaste mecánico de contactos); η=25a vida característica",
        },
        "modos_falla": [
            "Falla de apertura/cierre (mecanismo)",
            "Degradación del medio de extinción (SF6/vacío)",
            "Contaminación de aisladores",
        ],
        "sc_ids": ["VD-1-A", "VD-1-B", "VD-1-G"],
    },
    "cable_subterraneo": {
        "label":   "Cable subterráneo AT/MT",
        "icon":    "🔋",
        "bus_aplicables": ["TyD"],
        "metodologia_preferred": "asset_aware",
        "fallback_allowed": ["driver_based", "concept_rating"],
        "weibull_defaults": {
            "beta": 4.0,
            "eta":  30.0,
            "norma": "IEC 60502 / IEEE 400",
            "nota":  "β=4.0 (envejecimiento electrotérmico XLPE); η=30a vida de diseño",
        },
        "modos_falla": [
            "Degradación del aislamiento XLPE (tracción de agua)",
            "Falla de empalmes por envejecimiento",
            "Falla mecánica por sobrecarga térmica",
        ],
        "sc_ids": ["VD-1-A", "VD-1-B"],
    },
    "turbina_grupo_hidraulico": {
        "label":   "Turbina / grupo hidráulico",
        "icon":    "💧",
        "bus_aplicables": ["Generacion"],
        "metodologia_preferred": "asset_aware",
        "fallback_allowed": ["driver_based", "concept_rating"],
        "weibull_defaults": {
            "beta": 2.5,
            "eta":  40.0,
            "norma": "IEC 60994 / IEC 61116",
            "nota":  "β=2.5 (fatiga por ciclos de carga); η=40a vida típica turbinas Francis/Pelton",
        },
        "modos_falla": [
            "Cavitación de rodete / paletas directrices",
            "Fatiga de eje y cojinetes",
            "Degradación de sellos y laberintos",
        ],
        "sc_ids": ["VD-1-G", "VD-1-H"],
    },
    "tuberia_conduccion": {
        "label":   "Tubería de conducción (penstock / HDPE / acero / concreto)",
        "icon":    "🌊",
        "bus_aplicables": ["Generacion", "AyA", "Gas"],
        "metodologia_preferred": "asset_aware",
        "fallback_allowed": ["driver_based", "concept_rating"],
        "weibull_defaults": {
            "beta": 3.0,
            "eta":  45.0,
            "norma": "AWWA C200 / ISO 4427 / API 5L",
            "nota":  "β=3.0 (corrosión/erosión progresiva); η=45a vida acero + recubrimiento epoxi",
        },
        "modos_falla": [
            "Corrosión interna / externa (acero y concreto)",
            "Erosión hidráulica por sedimentos",
            "Fatiga por presión de agua (ciclos)",
            "Falla de juntas y accesorios",
        ],
        "sc_ids": ["VD-1-TUB"],
    },
    "bomba_impulsion": {
        "label":   "Bomba de impulsión",
        "icon":    "💦",
        "bus_aplicables": ["AyA"],
        "metodologia_preferred": "asset_aware",
        "fallback_allowed": ["driver_based", "concept_rating"],
        "weibull_defaults": {
            "beta": 2.2,
            "eta":  20.0,
            "norma": "HI 9.6.4 / ISO 9906",
            "nota":  "β=2.2 (desgaste mecánico rodete); η=20a vida bombas centrífugas industriales",
        },
        "modos_falla": [
            "Desgaste de rodete y voluta",
            "Falla de sellos mecánicos",
            "Falla de rodamientos por vibración",
        ],
        "sc_ids": ["VD-1-C", "VD-1-D"],
    },
    "valvula_regulador": {
        "label":   "Válvula / regulador de presión",
        "icon":    "🔧",
        "bus_aplicables": ["Gas", "AyA"],
        "metodologia_preferred": "asset_aware",
        "fallback_allowed": ["driver_based", "concept_rating"],
        "weibull_defaults": {
            "beta": 2.0,
            "eta":  20.0,
            "norma": "API 580 / ISO 15848",
            "nota":  "β=2.0 (desgaste de asiento y vástago); η=20a vida operativa en GN",
        },
        "modos_falla": [
            "Fuga por asiento desgastado",
            "Falla del actuador o piloto",
            "Corrosión del cuerpo",
        ],
        "sc_ids": ["VD-1-E", "VD-1-TUB"],
    },
    "compresor": {
        "label":   "Compresor (estación GN)",
        "icon":    "⚙️",
        "bus_aplicables": ["Gas"],
        "metodologia_preferred": "asset_aware",
        "fallback_allowed": ["driver_based", "concept_rating"],
        "weibull_defaults": {
            "beta": 2.5,
            "eta":  25.0,
            "norma": "API 687 / ISO 10816",
            "nota":  "β=2.5 (fatiga mecánica rotativa); η=25a vida de overhaul mayor",
        },
        "modos_falla": [
            "Falla de válvulas de succión/descarga",
            "Desgaste de pistones y anillos",
            "Falla de cojinetes por lubricación",
        ],
        "sc_ids": ["VD-1-E", "VD-2-A"],
    },
    "otro": {
        "label":   "Otro / Proyecto de red / expansión / TI",
        "icon":    "📁",
        "bus_aplicables": ["TyD", "Generacion", "AyA", "Gas", "Todos"],
        "metodologia_preferred": "driver_based",
        "fallback_allowed": ["concept_rating"],
        "weibull_defaults": None,
        "modos_falla": [],
        "sc_ids": [],
    },
}
"""Familias de activos con deterioro Weibull documentado.
Define el techo de precisión (metodologia_preferred) y los parámetros
de deterioro de referencia por norma industrial (weibull_defaults).
Un analista puede declarar un nivel inferior (fallback) cuando no
dispone de los datos requeridos; la brecha se registra en el snapshot."""

# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN B: DATACLASSES DE ENTRADA
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SubCriterionInput:
    """
    Datos de un sub-criterio evaluado.
    Tipo 'monetizable': delta_annual = cantidad física × price_per_unit → COP
    Tipo 'concept_rating': delta_annual = nivel 0-6 → curva-S → scale_max → COP
    weight = 1.0 por defecto. Ponderación emergente de la monetización.
    """
    id: str
    label: str
    type: str                         # "monetizable" | "concept_rating"
    delta_annual: float = 0.0         # Cantidad física o nivel CR (0-6)
    unit: str = ""
    weight: float = 1.0               # Siempre 1.0 salvo decisión ASPD
    price_per_unit: Optional[float] = None
    value_function: str = "linear"
    scale_max_COP: Optional[float] = None
    vf_inflection: float = 5.0
    vf_steepness: float = 1.5


@dataclass
class DriverInput:
    """Value Driver con sus sub-criterios."""
    id: str
    reto: str
    sub_criteria: list = field(default_factory=list)
    weight: float = 1.0               # Siempre 1.0 salvo decisión ASPD


@dataclass
class RiskInput:
    """Perfil de riesgo: Caso Base vs. Riesgo Residual."""
    baseline_consequence_level: int   # 1-5 combinado: nivel único, cuando no se usan tipos múltiples
    baseline_probability_level: int   # 1-5
    residual_consequence_level: int = 1
    residual_probability_level: int = 1
    # Copperleaf C55: niveles de consecuencia por tipo (multi-type CoF)
    # {"financiero": 3, "hs": 2, "ambiental": 1, "regulatorio": 2, "reputacional": 3}
    consequence_types: dict = field(default_factory=dict)
    residual_consequence_types: dict = field(default_factory=dict)
    # C55: PoF independiente por tipo de consecuencia
    # {"financiero": 3, "hs": 4, "ambiental": 2, "regulatorio": 3, "reputacional": 2}
    # Si está vacío se usa baseline_probability_level para todos los tipos (comportamiento por defecto)
    consequence_type_probs: dict = field(default_factory=dict)
    residual_consequence_type_probs: dict = field(default_factory=dict)
    # C55: Weibull PoF temporal (RF9 / wp_risk_value §3.1)
    # Si weibull_beta > 0, _calc_risk usa PoF(t) = 1 − exp(−(age/η)^β) en lugar de
    # la tasa exponencial fija risk_growth_rate.
    # Jerarquía: RiskInput fields > config weibull_params_by_asset > exponencial.
    weibull_beta: float = 0.0        # Parámetro de forma β (0 = desactivado / usar exponencial)
    weibull_eta: float  = 0.0        # Vida característica η en años (e.g. 40 para transformadores)
    asset_age_years: float = 0.0     # Edad actual del activo en años (desde instalación/última renovación)

    # ── Modo directo: el costo anual ya viene monetizado ──────────────────────
    # Existe para las inversiones donde la inacción NO es la falla de un activo.
    # En una expansión no hay activo que envejezca: no actuar significa demanda
    # no atendida, energía no suministrada y compensaciones al usuario, y esas
    # cifras se conocen en pesos por año. Obligarlas a pasar por la matriz 5×5
    # las degrada a un producto de dos niveles discretos que no puede igualar
    # el dato real.
    #
    # Si baseline_annual_COP > 0 el modo directo reemplaza a la matriz para
    # AMBOS lados y fuerza la rama exponencial, que es la que modela un costo
    # que crece a una tasa. Todo lo demás sigue igual: anualidad de Gordon,
    # descuento por rezago, escalamiento y cobro de exposición por diferimiento.
    baseline_annual_COP: float = 0.0   # Costo anual de NO intervenir, en pesos
    residual_annual_COP: float = 0.0   # Costo anual que queda tras intervenir


@dataclass
class CostInput:
    """Costos del proyecto con weight = -1.0 (flujos de salida)."""
    capex_COP: float = 0.0
    opex_incremental_COP_year: float = 0.0   # OPEX adicional (si aplica)
    weight: float = -1.0                      # Siempre -1.0
    estimate_class: str = "Class5"
    """Base sobre la que se construyó el CAPEX — incertidumbre de estimación.

    Class1 = oferta en firme · Class2 = cotización/FEED · Class3 = ing. conceptual
    Class4 = prefactibilidad · Class5 = analogía o juicio experto (default).

    Es un atributo de LA ALTERNATIVA, no de la corrida: dos alternativas de la
    misma necesidad pueden tener madurez de estimativo muy distinta, y esa
    diferencia es la que decide si la comparación entre ellas está resuelta.

    Las bandas asociadas viven en config["cost_estimate_uncertainty"]["classes"].
    Default deliberadamente en la clase más débil — declarar un buen estimativo
    es lo que da ventaja, no omitir la declaración.
    """
    capex_by_year: dict = field(default_factory=dict)
    """Distribución real del CAPEX por año calendario.
    Formato: {2026: 100_000, 2027: 200_000, 2028: 150_000}
    Si está vacío, el motor distribuye uniformemente sobre construction_years.
    Fuente preferida: datos reales del portafolio (Excel, PEGA, SAP)."""


@dataclass
class ProjectInput:
    """Entrada completa de un proyecto para evaluación."""
    id: str
    name: str
    business_unit: str                # TyD | AyA | Generacion | Gas | Todos
    investment_type: str              # Clave de INVESTMENT_TYPES
    costs: CostInput = field(default_factory=CostInput)
    execution_year: int = 2026
    optimal_year: int = 2026
    drivers: list = field(default_factory=list)
    risk: Optional[RiskInput] = None
    dependencies: list = field(default_factory=list)
    """IDs de proyectos que deben estar seleccionados ANTES que este (restricción
    simple: el padre debe estar en el portafolio, sin lag temporal mínimo).
    Para dependencias temporales con lag, usar precedent_project_id + min_lag_years."""

    is_mandatory: bool = False        # No discrecional (must-do)

    # ── Gasto Comprometido (Committed Spend) ─────────────────────
    is_committed: bool = False
    """Proyecto con contrato firmado/ODS emitida pero sin ejecución aún.
    Diferencia vs. is_mandatory:
      is_mandatory  → obligación estratégica/regulatoria (el PMO decide ejecutarlo)
      is_committed  → compromiso contractual existente (el proveedor ya está asignado)
    Efecto MILP (R11): fuerza x[i][t_committed] = 1 en el año más cercano a
    committed_year dentro del horizonte. El presupuesto comprometido se contabiliza
    separadamente en committed_summary para visibilidad del CAPEX no discrecional."""

    committed_year: int = 0
    """Año del compromiso contractual. 0 = usar execution_year como referencia.
    El MILP fijará el proyecto en el año del horizonte más cercano a este valor.
    Ingresado por el analista desde la ODS/contrato firmado, no desde SIGEAS."""

    # ── DEPENDENCIAS TEMPORALES — Copperleaf Precedence Constraints §7.4 ────
    precedent_project_id: str = ""
    """ID del proyecto PADRE que debe ejecutarse ANTES que este (proyecto hijo).
    Implementa la restricción de Precedencia Temporal de Copperleaf C55 §7.4:
    el hijo no puede iniciar hasta que el padre haya sido seleccionado en t - lag.

    Diferencia vs. dependencies:
      dependencies : el padre debe estar en el portafolio (sin restricción temporal)
      precedent_project_id: el padre debe ejecutarse ANTES (con lag mínimo)

    Caso EPM AyA: tubería_matriz.id → red_distribución.precedent_project_id
    Caso EPM Gas: gasoducto_troncal.id → red_barrio.precedent_project_id
    """

    min_lag_years: int = 0
    """Años mínimos de espera entre fin del padre y inicio del hijo.
    0 (default): el hijo puede iniciar el mismo año que el padre.
    1: el hijo debe esperar al menos 1 año después del padre.
    2: 2 años de espera — ej. tiempo de estabilización/pruebas.

    La restricción MILP es:
      sum_{k=1}^{t} x_hijo(k)  <=  sum_{k=1}^{t - min_lag} x_padre(k)
    """

    mutual_exclusion_ids: list = field(default_factory=list)
    """Lista de IDs de proyectos que son EXCLUYENTES con este.
    Si el Proyecto A tiene B en mutual_exclusion_ids → solo uno de los dos
    puede ser seleccionado en el portafolio. Se agregan a ambos automáticamente.
    El optimizador añade: x_A(t) + x_B(t') <= 1 para todo t, t'.

    Uso típico: dos soluciones alternativas para el mismo problema de red.
    IMPORTANTE: el sistema valida que no exista dependencia entre proyectos
    excluyentes (lo que generaría un deadlock lógico).
    """

    # ── TEMPORALIDAD C55 §5.3 ── Execution Duration & Benefit Lag ──────────
    construction_years: int = 1
    """Duración de construcción en años. Durante este período el proyecto
    consume CAPEX pero NO entrega beneficios. Los beneficios se descuentan
    desde (execution_year + construction_years), no desde execution_year.
    Ejemplo: proyecto iniciado 2026 con construction_years=3 → beneficios
    desde 2029. Afecta VNT y al optimizador MILP (CAPEX multivigencia)."""

    benefit_start_offsets: dict = field(default_factory=dict)
    """Offset adicional en años por Value Driver, contado desde el fin de
    construcción. Permite que distintos drivers maduren en momentos diferentes.
    Formato: {"VD-1": 0, "VD-2": 1, "VD-3": 2}
    Offset 0 (default) = beneficio desde el primer año post-construcción.
    Ejemplo real subestación nueva:
      VD-1 ENS/Calidad: offset=0 (opera desde el día 1)
      VD-3 Cobertura/conexiones: offset=1 (rampa de clientes nuevos)
      VD-4 Carbono: offset=0 (inmediato al entrar en operación)
    El analista configura estos offsets en el ProjectWizard."""

    # ── CAMPOS — Refactorización Industrial ──────────────────────────────
    asset_type: str = ""
    """Tipo de activo físico. Usado para seleccionar el horizonte correcto desde
    asset_horizons_by_bu en config. Ejemplos: 'transformador_potencia',
    'tuberia_asbesto_cemento', 'sistema_SCADA', 'red_gas', 'planta_hidro'.
    Cuando está vacío, se usa analysis_horizon_years del config como fallback."""

    exclusion_group_id: Optional[str] = None
    """ID de grupo de exclusión mutua estricta. Todos los proyectos con el mismo
    exclusion_group_id compiten entre sí: el MILP solo puede seleccionar
    exactamente uno (o ninguno si ninguno es rentable — Status Quo).
    Diferencia vs mutual_exclusion_ids: este mecanismo es de grupo N-way,
    mientras que mutual_exclusion_ids es par a par (A vs B, A vs C...).
    Uso típico: GIS vs AIS vs Refuerzo para la misma barra de la subestación."""

    # ── C55: Risk Tolerance — Must-Mitigate Unacceptable Risks ──────
    asset_id: str = ""
    """ID del activo físico que este proyecto interviene.
    Permite al MILP agrupar inversiones por activo para la restricción R8
    (Must-Mitigate): si el activo tiene riesgo ≥ risk_tolerance_level,
    al menos un proyecto que lo lleve a nivel aceptable DEBE seleccionarse.
    Formato recomendado: 'TyD-TR-001', 'AyA-PTAP-Norte', 'Gas-RE-Bello'.
    Si está vacío, el activo no participa en la restricción R8."""

    is_baseline: bool = False
    """Marca este proyecto como el caso base (Do Nothing / Status Quo).
    El baseline se evalúa primero y su VNT se usa como referencia para calcular
    el VNT marginal de cada alternativa: VNT_marginal = VNT_alt - VNT_baseline.
    El baseline captura el costo de riesgo puro e ingresos perdidos por inacción.
    Solo debe haber UN baseline por exclusion_group_id."""

    valuation_methodology: str = "driver_based"
    """Metodología de valoración usada para este proyecto. Controla el Badge
    Metodológico mostrado en la interfaz:
      'concept_rating'  — Cualitativo: escala 0-6, para beneficios no monetizables
      'driver_based'    — Métrico: delta físico × precio regulatorio (default)
      'asset_aware'     — Predictivo: Weibull/Degradación/EEOL integrado
    El rigor del badge guía al evaluador sobre la calidad del dato."""

    # ── CAMPO — Captura de Contexto del Problema ───────────────────────
    problem_description: str = ""
    """Descripción breve del problema técnico o necesidad que justifica la inversión.
    Máximo 256 caracteres. Campo obligatorio en el Wizard (valida antes de avanzar).
    Se persiste en el EvaluationSnapshot para trazabilidad ISO 55001 §4.5.
    Ejemplos:
      'Transformador T1 SE Norte alcanzó EEOL 2025; PoF=68%; riesgo=$3.2B COP.'
      'Red distribución Gas Barrio Enciso opera con 42% fugas; presión <min CREG.'
    """

    # NOTA: aquí existía baseline_risk_COP, declarado y jamás leído. Su
    # docstring afirmaba que evaluate_marginal() lo usaba, y no era cierto.
    # Se retiró: un campo muerto con documentación falsa es peor que ninguno,
    # porque quien implemente a partir de este archivo lo tomará por real.
    # Para declarar el costo anual de no actuar en pesos existe
    # RiskInput.baseline_annual_COP, que sí entra al cálculo.

    # ── CAMPO — Override directo de ΔR por portafolio ──────────────────
    delta_r_override_COP: float = 0.0
    """Override directo del ΔR (Riesgo Mitigado NPV) en COP.
    Cuando != 0, el motor usa este valor en lugar del calculado por _calc_risk(RiskInput).
    Se usa para los 4 portafolios distintos a Sostenimiento (Optimización, Expansión,
    Nuevas Soluciones, Adquisiciones) donde el ΔR se calcula directamente desde
    inputs específicos del portafolio en lugar de la matriz de riesgo 5×5.
    Unidad: COP (no MM COP). Positivo = beneficio (riesgo mitigado).

    ADVERTENCIA — lo que se pierde al usarlo. Este campo entrega el ΔR YA EN
    VALOR PRESENTE, así que el motor no llama a _calc_risk() en absoluto. Eso
    deja sin efecto TODO el tratamiento temporal: no escala por (1+g)^d al
    diferir, no cobra la exposición sufrida durante la espera, no acorta la
    ventana de evaluación y no aplica el descuento por rezago de obra. Un
    proyecto evaluado por esta vía responde siempre lo mismo sin importar
    cuándo se ejecute, de modo que el análisis de oportunidad queda inutilizado.

    Si lo que se conoce es el costo ANUAL de no actuar —el caso habitual en
    expansión: energía no suministrada, demanda no atendida, compensaciones—
    use RiskInput.baseline_annual_COP en su lugar. Recibe el monto anual,
    conserva las seis correcciones temporales y está vigilado por el grupo S21
    de la suite."""

    # ── Outage-Aligned Investment Scheduling ────────────────────────
    require_outage_alignment: bool = False
    """Si True, este proyecto SOLO puede ejecutarse en años que tengan al menos
    una PlannedOutage con asset_id == self.asset_id en el calendario corporativo.

    El MILP añade restricción R10: para todo t donde years[t] no sea una ventana
    de outage válida para este activo → x[i][t] == 0.

    Semántica:
      False (default)   → sin restricción de ventana (comportamiento actual)
      True + outages    → MILP elige el año de mayor VNT entre las ventanas disponibles
      True + sin outage → warning al analista; proyecto queda sin restricción R10

    Casos de uso EPM:
      - Reemplazo de transformador en subestación energizada (requiere salida programada)
      - Inspección interna de gasoducto (requiere parada de sección)
      - Rehabilitación de unidad de generación (parada mayor)
    """

    declared_execution_year: int = 0
    """Año de ejecución tal como fue declarado originalmente — antes de que
    cualquier escaneo del MILP o del año óptimo pruebe un año candidato
    distinto sobrescribiendo execution_year. Se captura una sola vez en
    __post_init__ si no se declara explícitamente. Es el ancla contra la que
    se mide el offset de cada tramo de costs.capex_by_year: sin este campo,
    un perfil de CAPEX explícito no puede reanclarse cuando el motor prueba
    un año de arranque distinto — se queda buscando literalmente los años
    calendario originales y el escaneo lo ve como si casi no costara nada
    en cualquier otro año."""

    def __post_init__(self):
        if self.declared_execution_year == 0:
            self.declared_execution_year = self.execution_year


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN B2: EARNED VALUE MANAGEMENT (Continuous Planning)
# Referencia: RFI §5 Gestión del Desempeño, filas 140–147
# Copperleaf Performance Management Module · PMI PMBOK EVM §7.4
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProjectActual:
    """
    Gastos reales y avance físico de un proyecto en ejecución.

    Se combina con ProjectInput para calcular las métricas EVM:
      PV  = Planned Value   (costo planificado acumulado a fecha de corte)
      AC  = Actual Cost     (costo real acumulado)
      EV  = Earned Value    (PV × pct_complete)
      SPI = EV / PV         (Schedule Performance Index)
      CPI = EV / AC         (Cost Performance Index)
      ETC = (capex_total − EV) / CPI   (Estimate to Complete)
      EAC = AC + ETC        (Estimate at Completion)
      SV  = EV − PV         (Schedule Variance)
      CV  = EV − AC         (Cost Variance)

    Uso en re-optimización continua (Capa 3, fase 2):
      pct_complete = 1.0 → proyecto completado; su AC consume presupuesto
                           pero ya no entra al MILP.
      0 < pct_complete < 1 → proyecto en curso; entra al MILP con capex = ETC
                              (el saldo por ejecutar), no con el capex original.
    """
    project_id: str
    """ID que debe coincidir exactamente con ProjectInput.id."""

    ac_COP: float
    """Costo Real acumulado (Actual Cost) en COP a la fecha de corte.
    Fuente: sistema ERP/SAP de EPM (extracto del período ejecutado)."""

    pct_complete: float
    """Porcentaje de avance físico [0.0 – 1.0].
    0.0 = no iniciado, 1.0 = completado.
    Fuente: informe de gestión de proyectos (avance por hitos)."""

    cut_year: int = 0
    """Año calendario de corte (ej. 2026). Si es 0 se infiere del año actual."""

    ev_COP: float = -1.0
    """Earned Value declarado explícitamente en COP.
    Si es -1.0 (default), el EVMEngine lo calcula como PV × pct_complete.
    Úsalo cuando EPM tenga un EV diferente del método porcentual
    (ej. EV por hitos con pesos distintos al costo planificado uniforme)."""

    milestone_date_actual: str = ""
    """Fecha real del último hito alcanzado (formato ISO: 'YYYY-MM-DD').
    Referencia: RFI fila 144 — Hitos reales."""

    benefit_actual_COP: float = 0.0
    """Beneficios reales obtenidos acumulados en COP.
    Referencia: RFI fila 145 — Beneficios reales.
    Permite comparar VNT planificado vs. VNT efectivamente realizado."""

    is_paused: bool = False
    """True si la ejecución está pausada.
    Referencia: RFI fila 146 — Pausar y reanudar.
    En re-optimización, el MILP congela AC y aplaza solo el ETC."""


@dataclass
class EVMResult:
    """
    Resultado completo de las métricas EVM para un proyecto.
    Producido por EVMEngine.compute().
    """
    project_id:   str
    project_name: str
    capex_total:  float   # Presupuesto total planificado (COP)
    pv_COP:       float   # Planned Value acumulado a fecha de corte
    ac_COP:       float   # Actual Cost acumulado
    ev_COP:       float   # Earned Value
    etc_COP:      float   # Estimate to Complete
    eac_COP:      float   # Estimate at Completion (AC + ETC)
    sv_COP:       float   # Schedule Variance (EV − PV)
    cv_COP:       float   # Cost Variance (EV − AC)
    spi:          float   # Schedule Performance Index (EV / PV)
    cpi:          float   # Cost Performance Index (EV / AC)
    pct_complete: float   # Avance físico [0–1]
    pct_spent:    float   # Porcentaje del presupuesto gastado (AC / capex_total)
    cut_year:     int
    health:       str     # "VERDE" | "AMARILLO" | "ROJO"
    health_detail: str    # Texto explicativo del estado de salud


class EVMEngine:
    """
    Motor de Earned Value Management para el portafolio EPM.

    Implementa las métricas del PMI PMBOK §7.4 / ANSI EIA-748 adaptadas
    al modelo de inversiones anuales de Copperleaf (un desembolso por año,
    no curva S continua).

    Referencia RFI: §5 Gestión del Desempeño, filas 140–147.
    """

    # Umbrales de salud (valores típicos EPM/Copperleaf)
    SPI_WARN  = 0.90   # SPI < 0.90 → AMARILLO
    SPI_CRIT  = 0.75   # SPI < 0.75 → ROJO
    CPI_WARN  = 0.90   # CPI < 0.90 → AMARILLO
    CPI_CRIT  = 0.80   # CPI < 0.80 → ROJO

    @staticmethod
    def compute(
        project: "ProjectInput",
        actual:  ProjectActual,
        cut_year: int = 0,
    ) -> EVMResult:
        """
        Calcula las métricas EVM para un proyecto dado sus gastos reales.

        Asume distribución uniforme del CAPEX entre execution_year y
        (execution_year + construction_years - 1). Si se necesita una curva S
        real, el llamador puede sobrescribir ev_COP en ProjectActual.

        Args:
            project:  ProjectInput con los datos planificados del proyecto.
            actual:   ProjectActual con AC y % de avance físico.
            cut_year: Año de corte para calcular PV. 0 = año actual.

        Returns:
            EVMResult con todas las métricas EVM y semáforo de salud.
        """
        import datetime
        if cut_year <= 0:
            cut_year = actual.cut_year if actual.cut_year > 0 \
                       else datetime.datetime.now().year

        capex_total = project.costs.capex_COP
        duration    = max(1, project.construction_years)
        exec_year   = project.execution_year

        # ── PV: Planned Value acumulado hasta cut_year ────────────────────────
        # Distribución uniforme del CAPEX por año de construcción.
        # Años de construcción: [exec_year, exec_year + duration - 1]
        capex_per_yr = capex_total / duration
        years_elapsed = max(0, cut_year - exec_year + 1)
        years_in_plan = min(years_elapsed, duration)
        pv_cop = capex_per_yr * years_in_plan

        # ── EV: Earned Value ─────────────────────────────────────────────────
        if actual.ev_COP >= 0:
            ev_cop = actual.ev_COP          # declarado explícitamente
        else:
            ev_cop = pv_cop * actual.pct_complete

        # ── Métricas derivadas ────────────────────────────────────────────────
        ac_cop  = actual.ac_COP
        sv_cop  = ev_cop - pv_cop           # < 0 → atrasado
        cv_cop  = ev_cop - ac_cop           # < 0 → sobre-costo

        spi = ev_cop / pv_cop if pv_cop > 0 else 1.0
        cpi = ev_cop / ac_cop if ac_cop > 0 else 1.0

        # ETC y EAC
        remaining_work = capex_total - ev_cop
        etc_cop = remaining_work / cpi if cpi > 0 else remaining_work
        eac_cop = ac_cop + etc_cop

        pct_spent = ac_cop / capex_total if capex_total > 0 else 0.0

        # ── Semáforo de salud ─────────────────────────────────────────────────
        eng = EVMEngine
        if spi < eng.SPI_CRIT or cpi < eng.CPI_CRIT:
            health = "ROJO"
            health_detail = (
                f"SPI={spi:.2f} {'⚠️ crítico' if spi < eng.SPI_CRIT else 'ok'} · "
                f"CPI={cpi:.2f} {'⚠️ crítico' if cpi < eng.CPI_CRIT else 'ok'}. "
                f"EAC={eac_cop/1e6:,.0f} MM vs presupuesto {capex_total/1e6:,.0f} MM. "
                f"Requiere acción correctiva inmediata."
            )
        elif spi < eng.SPI_WARN or cpi < eng.CPI_WARN:
            health = "AMARILLO"
            health_detail = (
                f"SPI={spi:.2f} · CPI={cpi:.2f}. "
                f"EAC={eac_cop/1e6:,.0f} MM COP. "
                f"Monitoreo reforzado recomendado."
            )
        else:
            health = "VERDE"
            health_detail = (
                f"SPI={spi:.2f} · CPI={cpi:.2f}. "
                f"Proyecto dentro de cronograma y presupuesto. "
                f"EAC={eac_cop/1e6:,.0f} MM COP."
            )

        if actual.is_paused:
            health_detail = f"[PAUSADO] {health_detail}"

        return EVMResult(
            project_id   = project.id,
            project_name = project.name,
            capex_total  = capex_total,
            pv_COP       = pv_cop,
            ac_COP       = ac_cop,
            ev_COP       = ev_cop,
            etc_COP      = etc_cop,
            eac_COP      = eac_cop,
            sv_COP       = sv_cop,
            cv_COP       = cv_cop,
            spi          = spi,
            cpi          = cpi,
            pct_complete = actual.pct_complete,
            pct_spent    = pct_spent,
            cut_year     = cut_year,
            health       = health,
            health_detail= health_detail,
        )

    @staticmethod
    def compute_portfolio(
        projects: List["ProjectInput"],
        actuals:  List[ProjectActual],
        cut_year: int = 0,
    ) -> Tuple[List[EVMResult], dict]:
        """
        Aplica EVM a todos los proyectos que tienen un actual asociado.

        Args:
            projects: Lista completa del portafolio.
            actuals:  Lista de ProjectActual (solo proyectos en ejecución).
            cut_year: Año de corte. 0 = año actual.

        Returns:
            results:   Lista de EVMResult para los proyectos con actuals.
            summary:   Dict con métricas agregadas del portafolio:
                       {pv_total, ac_total, ev_total, eac_total,
                        spi_portfolio, cpi_portfolio,
                        n_rojo, n_amarillo, n_verde,
                        budget_variance_COP, budget_variance_pct}
        """
        actual_map = {a.project_id: a for a in actuals}
        proj_map   = {p.id: p for p in projects}

        results = []
        for pid, act in actual_map.items():
            proj = proj_map.get(pid)
            if proj is None:
                continue
            results.append(EVMEngine.compute(proj, act, cut_year))

        if not results:
            return [], {}

        pv_t  = sum(r.pv_COP  for r in results)
        ac_t  = sum(r.ac_COP  for r in results)
        ev_t  = sum(r.ev_COP  for r in results)
        eac_t = sum(r.eac_COP for r in results)

        spi_p = ev_t / pv_t if pv_t > 0 else 1.0
        cpi_p = ev_t / ac_t if ac_t > 0 else 1.0

        n_rojo    = sum(1 for r in results if r.health == "ROJO")
        n_amarill = sum(1 for r in results if r.health == "AMARILLO")
        n_verde   = sum(1 for r in results if r.health == "VERDE")

        # budget_variance: cuánto costará más/menos el portafolio vs. plan
        capex_plan = sum(r.capex_total for r in results)
        bv_cop     = eac_t - capex_plan
        bv_pct     = bv_cop / capex_plan if capex_plan > 0 else 0.0

        summary = {
            "pv_total_COP":        pv_t,
            "ac_total_COP":        ac_t,
            "ev_total_COP":        ev_t,
            "eac_total_COP":       eac_t,
            "spi_portfolio":       spi_p,
            "cpi_portfolio":       cpi_p,
            "n_rojo":              n_rojo,
            "n_amarillo":          n_amarill,
            "n_verde":             n_verde,
            "budget_variance_COP": bv_cop,
            "budget_variance_pct": bv_pct,
            "cut_year":            cut_year,
            "n_projects":          len(results),
        }
        return results, summary


class ActualsLoader:
    """
    Carga gastos reales (actuals) desde Excel o CSV para alimentar el EVMEngine.

    El archivo de actuals debe tener al menos las columnas:
      project_id   — ID del proyecto (debe coincidir con el portafolio)
      ac_cop       — Costo real acumulado en COP (o MM COP)
      pct_complete — Avance físico [0–100 o 0.0–1.0]

    Columnas opcionales:
      cut_year, ev_cop, milestone_date_actual, benefit_actual_cop, is_paused

    Soporta los mismos alias tolerantes que PortfolioLoader.
    """

    _COL_ALIASES_ACT: Dict[str, List[str]] = {
        "project_id":   ["project_id","id","id_proyecto","proyecto_id","cod_proyecto","codigo"],
        "ac_cop":       ["ac_cop","ac","costo_real","costo real","actual_cost","gastos_reales",
                         "ejecutado_cop","ejecutado","gasto_real","gastado"],
        "pct_complete": ["pct_complete","pct_avance","avance","avance_fisico","avance fisico",
                         "porcentaje_avance","percent_complete","pct","completado","completitud"],
        "cut_year":     ["cut_year","anio_corte","año_corte","año corte","periodo_corte",
                         "corte","year"],
        "ev_cop":       ["ev_cop","ev","earned_value","valor_ganado","valor ganado"],
        "milestone_date_actual": ["milestone_date_actual","fecha_hito","fecha hito",
                                  "hito_real","milestone_actual"],
        "benefit_actual_cop":    ["benefit_actual_cop","beneficio_real","beneficio real",
                                  "beneficios_reales","beneficio_realizado"],
        "is_paused":    ["is_paused","pausado","paused","en_pausa","en pausa"],
    }

    def from_bytes(self, data: bytes, filename: str) -> Tuple[List[ProjectActual], dict]:
        """Carga actuals desde bytes (Streamlit file_uploader)."""
        ext = Path(filename).suffix.lower()
        if ext in (".xlsx", ".xls"):
            return self._from_excel(data)
        return self._from_csv(data)

    def from_path(self, path: str) -> Tuple[List[ProjectActual], dict]:
        with open(path, "rb") as f:
            return self.from_bytes(f.read(), path)

    def _map_cols(self, headers: List[str]) -> Dict[str, int]:
        """Mapea columnas con tolerancia a tildes y variantes."""
        result: Dict[str, int] = {}
        norm_hdrs = [_norm(h) for h in headers]
        for field, aliases in self._COL_ALIASES_ACT.items():
            for alias in aliases:
                alias_n = _norm(alias)
                if alias_n in norm_hdrs:
                    result[field] = norm_hdrs.index(alias_n)
                    break
        return result

    def _build_actuals(self, headers: List[str], rows: list) -> Tuple[List[ProjectActual], dict]:
        col_map  = self._map_cols(headers)
        warnings: List[str] = []
        results:  List[ProjectActual] = []

        if "project_id" not in col_map:
            warnings.append("Columna 'project_id' no encontrada — no se puede cargar actuals.")
            return [], {"warnings": warnings, "n_rows": 0, "n_actuals": 0}
        if "ac_cop" not in col_map:
            warnings.append("Columna 'ac_cop' (costo real) no encontrada.")
            return [], {"warnings": warnings, "n_rows": 0, "n_actuals": 0}

        def g(row, field, default=None):
            idx = col_map.get(field)
            if idx is None:
                return default
            v = row[idx] if idx < len(row) else default
            return v if v is not None else default

        for i, row in enumerate(rows):
            pid = str(g(row, "project_id", "")).strip()
            if not pid or pid.lower() in ("none", "nan", ""):
                continue
            try:
                # AC: acepta MM COP si el valor es pequeño (< 50000 → probablemente MM)
                ac_raw = float(str(g(row, "ac_cop", 0)).replace(",", "."))
                ac_cop = ac_raw * 1_000_000 if ac_raw < 50_000 else ac_raw

                pct_raw = float(str(g(row, "pct_complete", 0)).replace(",", "."))
                pct = pct_raw / 100.0 if pct_raw > 1.0 else pct_raw

                ev_raw  = g(row, "ev_cop", -1)
                ev_cop  = float(str(ev_raw).replace(",", ".")) if ev_raw not in (None, "", "None") else -1.0
                if ev_cop > 0 and ev_cop < 50_000:
                    ev_cop *= 1_000_000

                cut_raw = g(row, "cut_year", 0)
                cut_yr  = int(float(str(cut_raw))) if cut_raw else 0

                ben_raw = g(row, "benefit_actual_cop", 0)
                ben_cop = float(str(ben_raw).replace(",", ".")) if ben_raw else 0.0
                if ben_cop > 0 and ben_cop < 50_000:
                    ben_cop *= 1_000_000

                paused_raw = g(row, "is_paused", False)
                is_paused  = str(paused_raw).strip().lower() in ("true","1","sí","si","s","yes","y")

                milestone = str(g(row, "milestone_date_actual", "") or "").strip()

                results.append(ProjectActual(
                    project_id             = pid,
                    ac_COP                 = ac_cop,
                    pct_complete           = max(0.0, min(1.0, pct)),
                    cut_year               = cut_yr,
                    ev_COP                 = ev_cop,
                    milestone_date_actual  = milestone,
                    benefit_actual_COP     = ben_cop,
                    is_paused              = is_paused,
                ))
            except Exception as ex:
                warnings.append(f"Fila {i+1}: error al parsear proyecto '{pid}' — {ex}")

        return results, {
            "warnings":  warnings,
            "n_rows":    len(rows),
            "n_actuals": len(results),
            "mapped_cols": list(col_map.keys()),
        }

    def _from_excel(self, data: bytes) -> Tuple[List[ProjectActual], dict]:
        if not OPENPYXL_OK:
            raise ImportError("openpyxl no instalado. pip install openpyxl")
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        # Buscar hoja llamada "actuals" o "ejecutados"; sino la primera
        ws = None
        for name in wb.sheetnames:
            if _norm(name) in ("actuals", "ejecutados", "reales", "gastos_reales"):
                ws = wb[name]
                break
        if ws is None:
            ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return [], {"warnings": ["Hoja de actuals vacía"]}
        headers = [str(c).strip() if c is not None else "" for c in rows[0]]
        data_rows = [list(r) for r in rows[1:] if any(c is not None for c in r)]
        return self._build_actuals(headers, data_rows)

    def _from_csv(self, data: bytes) -> Tuple[List[ProjectActual], dict]:
        import csv
        text = data.decode("utf-8", errors="replace")
        # Detectar separador
        sep = ","
        for s in [";", "\t", "|"]:
            if text.count(s) > text.count(","):
                sep = s
                break
        reader = list(csv.reader(io.StringIO(text), delimiter=sep))
        if not reader:
            return [], {"warnings": ["CSV vacío"]}
        headers   = [h.strip() for h in reader[0]]
        data_rows = [row for row in reader[1:] if any(c.strip() for c in row)]
        return self._build_actuals(headers, data_rows)


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN B3: OUTAGE-ALIGNED SCHEDULING
# Referencia: RFI §3 "Alineamiento con las Interrupciones" / §3 "Interrupciones
# programadas" / §4 "Respetando asociaciones de interrupciones planificadas"
# Copperleaf C55 §5.4 — Planned Outage Calendar · ISO 55001 §8.3
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PlannedOutage:
    """Ventana de mantenimiento/interrupción programada para un activo físico.

    El motor cruza PlannedOutage.asset_id con ProjectInput.asset_id: si un
    proyecto tiene require_outage_alignment=True, solo puede ejecutarse en
    años donde exista al menos un PlannedOutage con el mismo asset_id.

    Fuente de datos:
      - config.json → sección "planned_outages" (calendario corporativo EPM)
      - Override por hoja "Interrupciones" en el Excel de portafolio
      - Editor manual en pestaña Config de la app

    Integración SAP PM / SIGEAS:
      El asset_id debe coincidir con el identificador funcional del activo en
      el EAM corporativo (ej. 'TyD-TR-001' = equipo funcional en SAP PM).
    """
    outage_id: str
    """Identificador único. Formato recomendado: 'OUT-<BU>-<ACTIVO>-<AÑO>'
    Ejemplo: 'OUT-TyD-SE-Norte-TR001-2026'"""

    asset_id: str
    """ID del activo físico. Debe coincidir con ProjectInput.asset_id."""

    year: int
    """Año calendario en que está programada la ventana de mantenimiento."""

    name: str = ""
    """Nombre descriptivo. Ej: 'Mantenimiento Mayor TR-001 SE Norte'."""

    duration_days: int = 0
    """Duración estimada de la ventana en días. 0 = no especificada.
    Usado solo para visualización; no afecta la restricción MILP."""

    outage_type: str = "maintenance"
    """Tipo de interrupción:
      'maintenance'  → Mantenimiento mayor programado (PMO)
      'forced'       → Salida forzada / emergencia planificada
      'regulatory'   → Parada requerida por regulador (CREG, ANLA, SUI)
    """


class OutageCalendar:
    """Índice de ventanas de outage para consulta eficiente en el MILP.

    Construido una sola vez por optimize() a partir de la lista de PlannedOutage.
    Responde: "¿qué años tiene este asset_id disponibles para ejecución?"
    """

    def __init__(self, outages: List[PlannedOutage]):
        # asset_id → sorted list of valid execution years
        self._by_asset: Dict[str, List[int]] = {}
        for o in (outages or []):
            self._by_asset.setdefault(o.asset_id, []).append(o.year)
        for k in self._by_asset:
            self._by_asset[k] = sorted(set(self._by_asset[k]))

    def valid_years(self, asset_id: str) -> List[int]:
        """Años con outage programado para este asset_id. Lista vacía = sin ventanas."""
        return self._by_asset.get(asset_id, [])

    def has_any(self, asset_id: str) -> bool:
        return bool(self._by_asset.get(asset_id))

    def all_asset_ids(self) -> List[str]:
        return list(self._by_asset.keys())


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN B4: WORKFLOW DE APROBACIÓN
# Referencia: RFI §8 "Flujos de Trabajo" · Copperleaf C55 §7 — Investment Lifecycle
# ISO 55001 §8.1 Planificación y control operacional · §9.1 Seguimiento
#
# v1 (actual): roles declarativos en sidebar — sin autenticación (Option 1)
# v2 (futura): migrar get_role() a catálogo config["users"] (Option 2)
#   El audit trail (WorkflowTransition.actor/role) no cambia entre versiones.
# ══════════════════════════════════════════════════════════════════════════════

# ── Estados del ciclo de vida de una inversión ────────────────────────────────
WF_STATES = {
    "BORRADOR":            "📝 Borrador",
    "ENVIADO":             "📤 Enviado a revisión",
    "EN_REVISION":         "🔍 En revisión técnica",
    "APROBADO_NEGOCIO":    "✅ Aprobado por negocio",
    "APROBADO_PORTAFOLIO": "🏆 Aprobado en portafolio",
    "DIFERIDO":            "⏸️ Diferido al siguiente ciclo",
    "RECHAZADO":           "❌ Rechazado",
    "EN_EJECUCION":        "🚧 En ejecución",
    "COMPLETADO":          "🎯 Completado",
}

# ── Transiciones válidas por rol ──────────────────────────────────────────────
# Formato: (estado_actual, rol_requerido) → (estado_siguiente, label_acción, icono)
WF_TRANSITIONS: Dict[tuple, tuple] = {
    # Analista — crea y somete
    ("BORRADOR",         "analista"):       ("ENVIADO",             "Someter a revisión",      "📤"),
    ("BORRADOR",         "coordinador_ga"): ("ENVIADO",             "Someter a revisión",      "📤"),
    ("RECHAZADO",        "analista"):       ("BORRADOR",            "Reabrir como borrador",   "📝"),
    ("DIFERIDO",         "analista"):       ("BORRADOR",            "Reabrir como borrador",   "📝"),
    # Coordinador GA — revisión técnica
    ("ENVIADO",          "coordinador_ga"): ("EN_REVISION",         "Aceptar para revisión",   "🔍"),
    ("EN_REVISION",      "coordinador_ga"): ("APROBADO_NEGOCIO",    "Aprobar (negocio)",        "✅"),
    ("EN_REVISION",      "coordinador_ga"): None,   # placeholder — múltiples acciones, ver abajo
    ("APROBADO_NEGOCIO", "coordinador_ga"): ("EN_REVISION",         "Devolver a revisión",     "↩️"),
    # Comité GA — aprobación portafolio
    ("APROBADO_NEGOCIO", "comite_ga"):      ("APROBADO_PORTAFOLIO", "Aprobar para portafolio", "🏆"),
    ("APROBADO_NEGOCIO", "comite_ga"):      None,   # placeholder — diferir/rechazar abajo
    ("DIFERIDO",         "comite_ga"):      ("APROBADO_NEGOCIO",    "Reactivar al ciclo",      "🔄"),
    # Director — override en cualquier estado
    ("ENVIADO",          "director"):       ("RECHAZADO",           "Rechazar (override)",     "❌"),
    ("EN_REVISION",      "director"):       ("RECHAZADO",           "Rechazar (override)",     "❌"),
    ("APROBADO_NEGOCIO", "director"):       ("RECHAZADO",           "Rechazar (override)",     "❌"),
    ("APROBADO_NEGOCIO", "director"):       ("APROBADO_PORTAFOLIO", "Aprobar (override)",      "🏆"),
}

# Tabla completa de transiciones (sin placeholders None)
WF_FULL_TRANSITIONS: List[Dict] = [
    # analista
    {"from": "BORRADOR",         "role": "analista",       "to": "ENVIADO",             "label": "Someter a revisión",      "icon": "📤"},
    {"from": "RECHAZADO",        "role": "analista",       "to": "BORRADOR",            "label": "Reabrir como borrador",   "icon": "📝"},
    {"from": "DIFERIDO",         "role": "analista",       "to": "BORRADOR",            "label": "Reabrir como borrador",   "icon": "📝"},
    # coordinador_ga
    {"from": "BORRADOR",         "role": "coordinador_ga", "to": "ENVIADO",             "label": "Someter a revisión",      "icon": "📤"},
    {"from": "ENVIADO",          "role": "coordinador_ga", "to": "EN_REVISION",         "label": "Aceptar para revisión",   "icon": "🔍"},
    {"from": "EN_REVISION",      "role": "coordinador_ga", "to": "APROBADO_NEGOCIO",    "label": "Aprobar (negocio)",        "icon": "✅"},
    {"from": "EN_REVISION",      "role": "coordinador_ga", "to": "BORRADOR",            "label": "Devolver al analista",    "icon": "↩️"},
    {"from": "EN_REVISION",      "role": "coordinador_ga", "to": "RECHAZADO",           "label": "Rechazar",                "icon": "❌"},
    {"from": "APROBADO_NEGOCIO", "role": "coordinador_ga", "to": "EN_REVISION",         "label": "Devolver a revisión",     "icon": "↩️"},
    # comite_ga
    {"from": "APROBADO_NEGOCIO", "role": "comite_ga",      "to": "APROBADO_PORTAFOLIO", "label": "Aprobar para portafolio", "icon": "🏆"},
    {"from": "APROBADO_NEGOCIO", "role": "comite_ga",      "to": "DIFERIDO",            "label": "Diferir al siguiente ciclo", "icon": "⏸️"},
    {"from": "APROBADO_NEGOCIO", "role": "comite_ga",      "to": "RECHAZADO",           "label": "Rechazar",                "icon": "❌"},
    {"from": "DIFERIDO",         "role": "comite_ga",      "to": "APROBADO_NEGOCIO",    "label": "Reactivar al ciclo",      "icon": "🔄"},
    # director — override
    {"from": "ENVIADO",          "role": "director",       "to": "RECHAZADO",           "label": "Rechazar (override)",     "icon": "❌"},
    {"from": "EN_REVISION",      "role": "director",       "to": "RECHAZADO",           "label": "Rechazar (override)",     "icon": "❌"},
    {"from": "EN_REVISION",      "role": "director",       "to": "APROBADO_NEGOCIO",    "label": "Aprobar negocio (override)", "icon": "✅"},
    {"from": "APROBADO_NEGOCIO", "role": "director",       "to": "APROBADO_PORTAFOLIO", "label": "Aprobar portafolio (override)", "icon": "🏆"},
    {"from": "APROBADO_NEGOCIO", "role": "director",       "to": "RECHAZADO",           "label": "Rechazar (override)",     "icon": "❌"},
    {"from": "DIFERIDO",         "role": "director",       "to": "APROBADO_PORTAFOLIO", "label": "Aprobar portafolio (override)", "icon": "🏆"},
    # ── Ejecución ────────────────────────────────────────────────────────────
    # EN_EJECUCION y COMPLETADO estaban definidos como estados, listados como
    # terminales y pintados en la interfaz, pero ninguna transición llegaba a
    # ellos: el flujo moría en APROBADO_PORTAFOLIO. Eso dejaba cortada la cadena
    # con el módulo EVM de planificación continua, que se alimenta justamente de
    # los proyectos en ejecución.
    {"from": "APROBADO_PORTAFOLIO", "role": "coordinador_ga", "to": "EN_EJECUCION",  "label": "Iniciar ejecución",        "icon": "🚧"},
    {"from": "APROBADO_PORTAFOLIO", "role": "director",       "to": "EN_EJECUCION",  "label": "Iniciar ejecución",        "icon": "🚧"},
    {"from": "APROBADO_PORTAFOLIO", "role": "comite_ga",      "to": "DIFERIDO",      "label": "Diferir antes de ejecutar", "icon": "⏸️"},
    {"from": "EN_EJECUCION",        "role": "coordinador_ga", "to": "COMPLETADO",    "label": "Cerrar proyecto",          "icon": "🎯"},
    {"from": "EN_EJECUCION",        "role": "director",       "to": "COMPLETADO",    "label": "Cerrar proyecto",          "icon": "🎯"},
    {"from": "EN_EJECUCION",        "role": "director",       "to": "DIFERIDO",      "label": "Suspender ejecución",      "icon": "⏸️"},
]

# Estados que cierran el ciclo — no admiten más transiciones.
# APROBADO_PORTAFOLIO y EN_EJECUCION dejaron de ser terminales: ahora continúan
# hacia ejecución y cierre. RECHAZADO y DIFERIDO no son terminales porque el
# analista puede reabrirlos; se conservan aquí solo RECHAZADO por compatibilidad
# con la lógica de valid_actions, que lo exceptúa explícitamente.
WF_TERMINAL_STATES = {"RECHAZADO", "COMPLETADO"}

# Color de estado para la UI
WF_STATE_COLOR = {
    "BORRADOR":            "#64748B",
    "ENVIADO":             "#3B82F6",
    "EN_REVISION":         "#F59E0B",
    "APROBADO_NEGOCIO":    "#22C55E",
    "APROBADO_PORTAFOLIO": "#10B981",
    "DIFERIDO":            "#A78BFA",
    "RECHAZADO":           "#EF4444",
    "EN_EJECUCION":        "#0EA5E9",
    "COMPLETADO":          "#6EE7B7",
}


@dataclass
class WorkflowTransition:
    """Registro inmutable de una transición de estado en el ciclo de vida del proyecto.

    Compatible con SnapshotStore (ISO 55001 §4.5): cada transición puede
    vincularse a un snapshot SHA-256 del ProjectInput en ese momento.

    v1→v2: actor es el nombre declarado (Option 1); en v2 será el username
    verificado del catálogo config["users"]. El campo no cambia de nombre.
    """
    from_state:   str
    to_state:     str
    actor:        str        # Nombre declarado (v1) o username catálogo (v2)
    role:         str        # Rol en el momento de la acción
    bu:           str        # Negocio del actor
    timestamp:    str        # ISO 8601 — datetime.now().isoformat()
    label:        str        # Etiqueta de la acción ejecutada
    comment:      str = ""   # Comentario libre (requerido para rechazos)
    snapshot_sha: str = ""   # SHA-256 del ProjectInput si se generó snapshot


@dataclass
class ProjectWorkflow:
    """Estado de aprobación de un proyecto de inversión.

    Se almacena en session_state["workflows"][project_id].
    No modifica ProjectInput — es un overlay desacoplado.
    """
    project_id:        str
    project_name:      str
    bu:                str
    current_state:     str = "BORRADOR"
    cycle:             str = ""   # Ciclo presupuestal: "PAI-2026", "PAI-2027"…
    assigned_reviewer: str = ""   # Coordinador GA asignado (informativo)
    due_date:          str = ""   # Fecha límite del ciclo (YYYY-MM-DD)
    history:           List[WorkflowTransition] = field(default_factory=list)

    def __post_init__(self):
        # `current_state` va antes que `cycle` en el orden de campos, así que
        # un llamado posicional (id, nombre, bu, "PAI 2026") mete el ciclo en el
        # estado. Sin validación eso crea un workflow en un estado inexistente:
        # no aparece en los conteos, ninguna transición le aplica, y nadie se
        # entera — el proyecto simplemente desaparece del flujo de aprobación.
        if self.current_state not in WF_STATES:
            raise ValueError(
                f"Estado de workflow inválido: '{self.current_state}'. "
                f"Válidos: {', '.join(WF_STATES)}. "
                f"Si querías fijar el ciclo presupuestal, usa cycle='{self.current_state}' "
                f"— `current_state` va antes que `cycle` en el orden de campos."
            )

    def last_transition(self) -> Optional[WorkflowTransition]:
        return self.history[-1] if self.history else None

    def last_comment(self) -> str:
        for t in reversed(self.history):
            if t.comment:
                return t.comment
        return ""


class WorkflowEngine:
    """Motor de transiciones del workflow de aprobación de inversiones.

    Diseño:
      - Sin estado propio (stateless) — opera sobre ProjectWorkflow
      - valid_actions(): qué puede hacer este rol sobre este estado
      - apply(): aplica una transición y devuelve el ProjectWorkflow actualizado
      - pending_for_role(): bandeja de pendientes filtrada por rol y BU
      - summary(): métricas de portafolio por estado

    v1→v2: get_role() es el único punto de cambio para migrar autenticación.
    """

    @staticmethod
    def valid_actions(role: str, state: str) -> List[Dict]:
        """Retorna la lista de transiciones que este rol puede ejecutar en este estado."""
        # RECHAZADO y DIFERIDO figuran como cierre del flujo pero admiten
        # reapertura: quién puede hacerlo lo define la tabla de transiciones,
        # no una excepción aquí. COMPLETADO sí cierra de forma definitiva.
        if state in WF_TERMINAL_STATES and state not in ("RECHAZADO", "DIFERIDO"):
            return []
        return [
            t for t in WF_FULL_TRANSITIONS
            if t["from"] == state and t["role"] == role
        ]

    @staticmethod
    def apply(
        wf: ProjectWorkflow,
        action: Dict,
        actor: str,
        role: str,
        bu: str,
        comment: str = "",
        snapshot_sha: str = "",
        project: Optional["ProjectInput"] = None,
        capex_final_COP: Optional[float] = None,
        accuracy_store: Optional["EstimateAccuracyStore"] = None,
    ) -> ProjectWorkflow:
        """Aplica una transición válida y retorna el workflow actualizado.

        Raises ValueError si la transición no está permitida para este rol/estado.

        Args:
            project, capex_final_COP, accuracy_store: opcionales, solo importan
                cuando action["to"] == "COMPLETADO". Si los tres están presentes,
                se registra automáticamente la desviación del estimativo — es el
                único momento en que el costo final es definitivo, y es la señal
                de la que se calibran las bandas propias de EPM en
                cost_estimate_uncertainty.classes_by_bu.
                Si falta alguno, el cierre simplemente no se registra: no es un
                error, es una omisión silenciosa deliberada, porque no todo
                caller tiene el costo final a mano en el momento de cerrar.
        """
        valid = WorkflowEngine.valid_actions(role, wf.current_state)
        match = next(
            (t for t in valid
             if t["to"] == action["to"] and t["label"] == action["label"]),
            None,
        )
        if match is None:
            raise ValueError(
                f"Transición '{action['label']}' no permitida para rol='{role}' "
                f"en estado='{wf.current_state}'."
            )
        # Validar comentario obligatorio en rechazos
        if action["to"] in ("RECHAZADO", "DIFERIDO") and not comment.strip():
            raise ValueError(
                "Se requiere un comentario para rechazar o diferir un proyecto."
            )

        transition = WorkflowTransition(
            from_state=wf.current_state,
            to_state=action["to"],
            actor=actor,
            role=role,
            bu=bu,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            label=action["label"],
            comment=comment.strip(),
            snapshot_sha=snapshot_sha,
        )
        wf.history.append(transition)
        wf.current_state = action["to"]

        if (action["to"] == "COMPLETADO" and project is not None
                and capex_final_COP is not None and accuracy_store is not None):
            try:
                accuracy_store.record_closure(
                    project, capex_final_COP=capex_final_COP, closed_by=actor)
            except Exception:
                # El cierre del workflow no puede fallar por un problema de
                # persistencia secundaria — se pierde la observación de este
                # proyecto, no la transición del usuario.
                pass

        return wf

    @staticmethod
    def pending_for_role(
        workflows: Dict[str, ProjectWorkflow],
        role: str,
        bu: str = "Todos",
    ) -> List[ProjectWorkflow]:
        """Bandeja de pendientes: proyectos que requieren acción de este rol."""
        result = []
        for wf in workflows.values():
            if bu != "Todos" and wf.bu != bu and role not in ("comite_ga", "director"):
                continue
            if WorkflowEngine.valid_actions(role, wf.current_state):
                result.append(wf)
        return sorted(result, key=lambda w: w.last_transition().timestamp
                      if w.last_transition() else "", reverse=True)

    @staticmethod
    def summary(workflows: Dict[str, ProjectWorkflow]) -> Dict[str, int]:
        """Conteo de proyectos por estado."""
        counts: Dict[str, int] = {s: 0 for s in WF_STATES}
        for wf in workflows.values():
            counts[wf.current_state] = counts.get(wf.current_state, 0) + 1
        return counts

    @staticmethod
    def get_workflow_schema() -> dict:
        """Exporta el esquema completo del workflow para documentación y UI.

        Returns:
            dict con:
              states       — {estado: descripción_legible}
              roles        — lista de roles del sistema
              transitions  — lista completa de transiciones {from, role, to, label, icon}
              terminal     — conjunto de estados terminales (sin salida normal)
        """
        roles = sorted({t["role"] for t in WF_FULL_TRANSITIONS})
        return {
            "states": dict(WF_STATES),
            "roles": roles,
            "transitions": list(WF_FULL_TRANSITIONS),
            "terminal": list(WF_TERMINAL_STATES),
            "initial_state": "BORRADOR",
        }

    @staticmethod
    def ensure_workflow(
        workflows: Dict[str, ProjectWorkflow],
        project_id: str,
        project_name: str,
        bu: str,
        cycle: str = "",
    ) -> ProjectWorkflow:
        """Crea un workflow en BORRADOR si no existe todavía para este proyecto."""
        if project_id not in workflows:
            workflows[project_id] = ProjectWorkflow(
                project_id=project_id,
                project_name=project_name,
                bu=bu,
                cycle=cycle,
            )
        return workflows[project_id]


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN B5: PRECISIÓN DE ESTIMATIVOS — desviación real por cierre de proyecto
# ══════════════════════════════════════════════════════════════════════════════
# Cierra el pendiente más urgente de la incertidumbre de estimación: las bandas
# de cost_estimate_uncertainty.classes son referencia de industria (AACE
# RP 18R-97), no calibración EPM, y lo seguirán siendo mientras nadie registre
# cuánto se desvió realmente cada proyecto.
#
# El EVMEngine ya calcula esa desviación (ac_COP vs. capex_total) y hoy se usa
# solo como semáforo de ejecución y se descarta. Este módulo persiste el dato
# en el único momento en que es definitivo — el cierre del proyecto— para que
# deje de perderse.
#
# Es el único pendiente de los tres discutidos cuyo costo crece con el tiempo:
# cada proyecto que cierra sin registrar su desviación es una observación que
# no se recupera nunca. Las bandas propias por negocio (classes_by_bu) y una
# eventual capa de escenarios de mundo dependen de que este módulo acumule
# datos — no tiene sentido construirlas antes.
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EstimateClosure:
    """Registro inmutable de la precisión de un estimativo al cierre del proyecto.

    Una fila = un proyecto cerrado. `deviation_pct` es lo único que importa
    para calibrar bandas; el resto es contexto para poder segmentar por
    negocio, tipo de intervención y clase declarada.
    """
    project_id: str
    project_name: str
    business_unit: str
    investment_type: str
    asset_class: str
    estimate_class: str
    """Base de estimativo declarada en CostInput al momento de capturar el
    proyecto. Es la clase que se está calificando con este cierre."""
    capex_initial_COP: float
    """CAPEX planificado — ProjectInput.costs.capex_COP en la versión que se
    aprobó, no un valor re-estimado a mitad de ejecución."""
    capex_final_COP: float
    """Costo real acumulado al cierre — EVMResult.ac_COP con pct_complete=1.0."""
    deviation_pct: float
    """(capex_final − capex_initial) / capex_initial. Positivo = sobrecosto,
    que es el sesgo esperado en proyectos de infraestructura."""
    closed_at: str
    closed_by: str


class EstimateAccuracyStore:
    """Almacén append-only de precisión de estimativos, calcado de SnapshotStore.

    Uso:
        store = EstimateAccuracyStore("./estimate_accuracy")
        store.record_closure(project, capex_final_COP=eac, closed_by="coord_ga")
        bands = store.bands_by_class(business_unit="TyD")   # {} si aún no hay dato suficiente

    `bands_by_class()` devuelve la estructura exacta que espera
    cost_estimate_uncertainty.classes_by_bu[BU] en DEFAULT_CONFIG, para que
    calibrar las bandas propias sea copiar el resultado a config.json el día
    que haya muestra suficiente — no un ejercicio de análisis aparte.
    """

    MIN_SAMPLES_DEFAULT = 15
    """Piso de observaciones por (negocio × clase) antes de que una banda deje
    de ser 'referencia de industria' y pueda declararse calibrada con datos
    EPM. Por debajo de esto, una banda 'propia' sería una simulación con
    muestra insuficiente — el mismo problema que se está corrigiendo."""

    def __init__(self, storage_dir: str = "./estimate_accuracy"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.storage_dir / "_index.json"
        self._index = self._load_index()

    def _load_index(self) -> dict:
        if self._index_path.exists():
            with open(self._index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"closures": [], "created": datetime.now(timezone.utc).isoformat()}

    def _save_index(self) -> None:
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)

    def record_closure(
        self,
        project: "ProjectInput",
        capex_final_COP: float,
        closed_by: str = "sistema",
        closed_at: str = "",
    ) -> EstimateClosure:
        """Registra el cierre de un proyecto. Idempotente por project_id: una
        segunda llamada para el mismo ID actualiza en vez de duplicar, porque
        un cierre puede reabrirse y volver a cerrarse en el workflow."""
        capex_initial = float(getattr(project.costs, "capex_COP", 0.0) or 0.0)
        deviation = ((capex_final_COP - capex_initial) / capex_initial
                     if capex_initial > 0 else 0.0)

        closure = EstimateClosure(
            project_id=project.id,
            project_name=project.name,
            business_unit=project.business_unit,
            investment_type=getattr(project, "investment_type", ""),
            asset_class=getattr(project, "asset_type", "") or "",
            estimate_class=getattr(project.costs, "estimate_class", None) or "Class5",
            capex_initial_COP=capex_initial,
            capex_final_COP=float(capex_final_COP),
            deviation_pct=deviation,
            closed_at=closed_at or datetime.now(timezone.utc).isoformat(),
            closed_by=closed_by,
        )

        self._index["closures"] = [
            c for c in self._index["closures"] if c.get("project_id") != project.id
        ]
        self._index["closures"].append(closure.__dict__)
        self._save_index()
        return closure

    def all_closures(self) -> List[dict]:
        return list(self._index.get("closures", []))

    def coverage(self) -> Dict[str, Dict[str, int]]:
        """Cuenta de cierres por (negocio, clase) — para saber qué falta."""
        cov: Dict[str, Dict[str, int]] = {}
        for c in self.all_closures():
            bu, cls = c.get("business_unit", "?"), c.get("estimate_class", "?")
            cov.setdefault(bu, {}).setdefault(cls, 0)
            cov[bu][cls] += 1
        return cov

    def bands_by_class(
        self,
        business_unit: Optional[str] = None,
        min_samples: int = MIN_SAMPLES_DEFAULT,
        low_pct: float = 0.10,
        high_pct: float = 0.90,
    ) -> Dict[str, dict]:
        """Bandas observadas por clase, en el formato de cost_estimate_uncertainty.classes.

        Solo devuelve una clase si tiene al menos `min_samples` cierres — por
        debajo de eso el percentil es ruido, no una banda. Las clases sin
        muestra suficiente simplemente no aparecen en el resultado; el
        llamador decide si completa con la referencia de industria.

        Args:
            business_unit: filtra a un negocio. None = todos los negocios juntos
                           (útil solo mientras cada negocio individual no tiene
                           muestra propia).
            low_pct, high_pct: percentiles que definen los extremos de la banda.
                           0.10/0.90 por defecto — más ceñido que un mínimo/máximo
                           crudo, que un solo cierre atípico distorsionaría.

        Returns:
            {"Class1": {"label": ..., "low": -0.08, "high": 0.15,
                        "description": "Observado sobre N=18 cierres EPM"}, ...}
        """
        closures = self.all_closures()
        if business_unit:
            closures = [c for c in closures if c.get("business_unit") == business_unit]

        by_class: Dict[str, List[float]] = {}
        for c in closures:
            by_class.setdefault(c.get("estimate_class", "Class5"), []).append(
                float(c.get("deviation_pct", 0.0)))

        result: Dict[str, dict] = {}
        for cls, devs in by_class.items():
            if len(devs) < min_samples:
                continue
            devs_sorted = sorted(devs)
            n = len(devs_sorted)

            def _pct(p: float) -> float:
                idx = min(n - 1, max(0, int(round(p * (n - 1)))))
                return devs_sorted[idx]

            result[cls] = {
                "label": f"{cls} — calibrado con datos EPM",
                "description": (
                    f"Observado sobre {n} cierres de proyecto"
                    f"{' en ' + business_unit if business_unit else ''} "
                    f"(P{int(low_pct*100)}/P{int(high_pct*100)})."
                ),
                "low": round(min(0.0, _pct(low_pct)), 4),
                "high": round(max(0.0, _pct(high_pct)), 4),
                "_n_samples": n,
                "_calibrado_con_datos_epm": True,
            }
        return result

    def readiness_report(
        self, min_samples: int = MIN_SAMPLES_DEFAULT
    ) -> List[dict]:
        """Por (negocio, clase): cuántos cierres hay y cuántos faltan para
        poder calibrar. Pensado para mostrarse tal cual en la pestaña Config."""
        cov = self.coverage()
        rows = []
        for bu, clases in sorted(cov.items()):
            for cls, n in sorted(clases.items()):
                rows.append({
                    "business_unit": bu,
                    "estimate_class": cls,
                    "n_closures": n,
                    "min_required": min_samples,
                    "ready": n >= min_samples,
                })
        return rows


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN C: VALUE ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class ValueEngine:
    """
    Motor de cálculo fiel a Copperleaf C55 VDM.

    Ecuación maestra (Copperleaf C55 §3.1):
        VNT(i) = Σ(w_r × VD_r × FD_lag)  −  CoD  +  ΔR  −  CAPEX_VP  −  ΔOPEX_NPV

    Componentes:
        Σ(w_r × VD_r × FD_lag) : beneficios ponderados, descontados por temporalidad
        FD_lag(driver)          : 1/(1+WACC)^(construction_years + offset_driver)
        CoD                     : beneficios_NPV / H_meses × meses_retraso
        ΔR                      : (Riesgo_Base − Riesgo_Residual) × Factor_NPV,
                                  menos la exposición sufrida si se difiere
        CAPEX_VP                : desembolso inflado y traído a valor presente
        ΔOPEX_NPV               : anualidad plana sobre el horizonte del activo
        (ambos costos con weight = -1.0)

    Decisiones de método que distinguen a este motor:
        — Eliminado TF (timing_factor) multiplicando beneficios: duplicaba el CoD.
        — ΔR separado del cálculo del CoD: era doble conteo.
        — Tres componentes independientes: benefits · CoD · ΔR.

    Todos los valores en COP. VNT puede ser negativo (destrucción de valor).
    VEI = VNT / CAPEX — índice de eficiencia para ranking de portafolio.
    """

    def __init__(self, config: dict):
        self.config = config
        self.wacc = config.get("wacc", 0.10)
        self.horizon = config.get("analysis_horizon_years", 10)
        self._factor_npv = self._calc_factor_npv()
        # ── Horizontes dinámicos por tipo de activo y BU ────────────────
        self.asset_horizons_by_bu: dict = config.get("asset_horizons_by_bu", {})
        """Estructura: {BU: {asset_type: horizon_years, ...}, ...}
        Consultado por get_horizon_for_asset() para calcular EAV correctamente."""
        # ── WACC por BU — Copperleaf C55 §3.2 ───────────────────────────────
        self.wacc_by_bu: dict = {
            k: v for k, v in config.get("wacc_by_bu", {}).items()
            if not k.startswith("_")   # ignorar claves de documentación "_nota"
        }
        # ── Horizonte de timing por BU — ventana escaneo t* por tipo de activo ──
        _ot_cfg = config.get("optimal_timing", {})
        self.horizon_years_by_bu: dict = {
            k: int(v) for k, v in _ot_cfg.get("horizon_years_by_bu", {}).items()
            if not k.startswith("_")
        }
        self._horizon_years_global: int = int(_ot_cfg.get("horizon_years", 5))

    def _get_wacc(self, business_unit: str = "") -> float:
        """Resuelve el WACC efectivo para la BU indicada.
        Jerarquía: wacc_by_bu[BU] > wacc_by_bu['Todos'] > self.wacc (global).
        """
        if self.wacc_by_bu:
            if business_unit and business_unit in self.wacc_by_bu:
                return float(self.wacc_by_bu[business_unit])
            if "Todos" in self.wacc_by_bu:
                return float(self.wacc_by_bu["Todos"])
        return self.wacc

    def _get_timing_horizon(self, business_unit: str = "") -> int:
        """Resuelve la ventana de timing óptimo (horizon_years) para la BU.
        Jerarquía: horizon_years_by_bu[BU] > horizon_years global.

        Esto garantiza que activos de larga vida (Generacion/AyA) usen una
        ventana mayor que activos eléctricos típicos (TyD/Gas), evitando que
        el escaneo t=0..5 corte antes del mínimo TOTEX real de activos con
        η≥40 años (turbinas, penstocks, tuberías de conducción).
        """
        if self.horizon_years_by_bu:
            if business_unit and business_unit in self.horizon_years_by_bu:
                return self.horizon_years_by_bu[business_unit]
        return self._horizon_years_global

    def _calc_factor_npv(self, horizon: int = None) -> float:
        """Factor de anualidad: convierte flujo anual constante a NPV.
        Si se proporciona horizon, usa ese valor; sino usa self.horizon."""
        h = horizon if horizon is not None else self.horizon
        if self.wacc == 0:
            return float(h)
        return (1 - (1 + self.wacc) ** (-h)) / self.wacc

    def _npv_annual(self, annual_cop: float, horizon: int = None) -> float:
        factor = self._calc_factor_npv(horizon) if horizon else self._factor_npv
        return annual_cop * factor


    # ── Horizontes dinámicos por tipo de activo ─────────────────────────
    def get_horizon_for_asset(self, project: "ProjectInput") -> int:
        """Retorna el horizonte de análisis apropiado para este proyecto.

        Jerarquía de lookup:
          1. asset_horizons_by_bu[BU][asset_type] — más específico
          2. asset_horizons_by_bu[BU]['_default'] — fallback por BU
          3. asset_horizons_by_bu['_global'][asset_type] — global por tipo
          4. analysis_horizon_years del config — fallback final

        Esto asegura que un transformador (30a) no compita en desventaja contra
        un sistema SCADA (5a) por simple acumulación de beneficios a largo plazo.
        """
        bu = project.business_unit
        at_raw = project.asset_type or ""
        ahbu = self.asset_horizons_by_bu

        # Normalizar: minúsculas + espacios → guión_bajo, sin acentos básicos
        def _norm_at(s: str) -> str:
            s = s.strip().lower()
            s = s.replace(" ", "_").replace("-", "_")
            # Transliterar tildes más comunes para tolerancia
            for src, dst in (("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),
                              ("ñ","n"),("ü","u")):
                s = s.replace(src, dst)
            return s

        at = _norm_at(at_raw)

        def _bu_lookup(bu_key: str, at_key: str) -> int | None:
            """Busca primero literal, luego normalizado."""
            node = ahbu.get(bu_key, {})
            if at_key in node:
                return int(node[at_key])
            # Intento normalizado sobre las claves del nodo
            norm_node = {_norm_at(k): v for k, v in node.items()}
            if at_key in norm_node:
                return int(norm_node[at_key])
            return None

        # 1. Específico BU + asset_type
        if bu in ahbu and at:
            v = _bu_lookup(bu, at)
            if v is not None:
                return v
        # 2. Fallback BU
        if bu in ahbu and "_default" in ahbu[bu]:
            return int(ahbu[bu]["_default"])
        # 3. Global por asset_type
        if "_global" in ahbu and at:
            v = _bu_lookup("_global", at)
            if v is not None:
                return v
        # 4. Fallback config
        return self.horizon

    # ── Valor Anual Equivalente (EAV) ──────────────────────────────────
    def calc_eav(self, vnt_cop: float, horizon: int) -> float:
        """Convierte VNT (NPV acumulado) en Valor Anual Equivalente.

        EAV = VNT × WACC / (1 − (1+WACC)^−H)

        Intuición: ¿cuánto vale este proyecto POR AÑO, en términos comparables?
        Un proyecto de 40 años con VNT=$800MM tiene EAV=$81MM/año.
        Un proyecto de 5 años con VNT=$200MM tiene EAV=$52MM/año.
        Sin EAV, el de 40 años "gana" injustamente por acumulación.
        """
        if horizon <= 0 or vnt_cop == 0:
            return 0.0
        if self.wacc == 0:
            return vnt_cop / horizon
        annuity_factor = self._calc_factor_npv(horizon)
        if annuity_factor == 0:
            return 0.0
        return vnt_cop / annuity_factor

    # ── VNT Marginal (alternativa vs baseline) ──────────────────────────
    def evaluate_marginal(self, alternative: "ProjectInput",
                          baseline: "ProjectInput") -> dict:
        """Calcula el VNT marginal de una alternativa respecto al baseline.

        VNT_marginal = VNT_alternativa − VNT_baseline

        El baseline (Do Nothing) captura el costo del riesgo puro y los ingresos
        perdidos por inacción. El VNT marginal es la GANANCIA NETA de actuar
        vs. no actuar.

        Si VNT_marginal < 0: la inversión destruye valor; el riesgo de la
        inacción es menor que el costo de la solución. Se genera alerta pedagógica.

        Returns:
            dict con vnt_baseline, vnt_alternative, vnt_marginal, eav_marginal,
            vei_eav, alerta_pedagogica (si aplica)
        """
        r_base = self.evaluate(baseline)
        r_alt  = self.evaluate(alternative)

        vnt_base  = r_base["net_value_COP"]
        vnt_alt   = r_alt["net_value_COP"]
        vnt_marg  = vnt_alt - vnt_base

        # EAV y VEI usan el WACC de la alternativa (no el global)
        _orig_wacc_marg = self.wacc
        self.wacc = self._get_wacc(alternative.business_unit)
        try:
            horizon  = self.get_horizon_for_asset(alternative)
            eav_marg = self.calc_eav(vnt_marg, horizon)
            capex    = alternative.costs.capex_COP
            # VEI_EAV: EAV / (CAPEX / horizon) — normaliza eficiencia entre horizontes
            vei_eav  = eav_marg / (capex / horizon) if capex > 0 and horizon > 0 else 0.0
        finally:
            self.wacc = _orig_wacc_marg

        alerta = None
        if vnt_marg < 0:
            _delay = max(0, alternative.execution_year - alternative.optimal_year)
            if _delay == 0:
                alerta = (
                    "⚠️ Inversión destructora de valor incluso en el momento óptimo: "
                    f"VNT Marginal = {vnt_marg/1e6:,.1f} MM COP. "
                    "El activo ya alcanzó su punto crítico — posponer agrava la pérdida. "
                    "Revisa el CAPEX, amplía los beneficios monetizables o evalúa "
                    "una solución de menor alcance técnico."
                )
            else:
                alerta = (
                    "⚠️ Inversión destructora de valor: El costo supera el riesgo de la inacción. "
                    f"VNT Marginal = {vnt_marg/1e6:,.1f} MM COP. "
                    "Revisa la magnitud de los beneficios, el CAPEX o el perfil de riesgo. "
                    "Considera reducir alcance o posponer hasta que el deterioro del activo "
                    "justifique la inversión (ver Tiempo Óptimo de Intervención)."
                )

        return {
            "baseline_id": baseline.id,
            "alternative_id": alternative.id,
            "vnt_baseline_COP": vnt_base,
            "vnt_alternative_COP": vnt_alt,
            "vnt_marginal_COP": vnt_marg,
            "eav_marginal_COP": eav_marg,
            "vei_eav": vei_eav,
            "horizon_used": horizon,
            "alerta_pedagogica": alerta,
            "baseline_result": r_base,
            "alternative_result": r_alt,
        }

    def _vf_linear(self, score: float, scale_max: float) -> float:
        return (score / 10.0) * scale_max

    def _vf_scurve(self, score: float, scale_max: float,
                   inflection: float = 5.0, steepness: float = 1.5) -> float:
        k = steepness
        x0 = inflection
        sigmoid = 1.0 / (1.0 + math.exp(-k * (score - x0)))
        sigmoid_0 = 1.0 / (1.0 + math.exp(-k * (0 - x0)))
        sigmoid_10 = 1.0 / (1.0 + math.exp(-k * (10 - x0)))
        normalized = (sigmoid - sigmoid_0) / (sigmoid_10 - sigmoid_0)
        return normalized * scale_max

    def _apply_vf(self, sc: SubCriterionInput) -> float:
        """Aplica la Value Function. Concept Rating: nivel 0-10 → score [-10,+10].

        C55: escala 0-10 con neutral en 5 (alineado con C55 §4.1).
        Anterior: 0-6 con neutral en 3.
        """
        if sc.scale_max_COP is None:
            return 0.0
        if sc.type == "concept_rating":
            baseline = 5.0   # neutral C55 (antes: 3.0)
            max_range = 5.0  # rango máximo C55 (antes: 3.0)
            delta = sc.delta_annual - baseline
            score = (delta / max_range) * 10.0
            sign = 1.0 if score >= 0 else -1.0
            if sc.value_function == "s_curve":
                return sign * self._vf_scurve(abs(score), sc.scale_max_COP,
                                               sc.vf_inflection, sc.vf_steepness)
            return self._vf_linear(score, sc.scale_max_COP)
        else:
            score = sc.delta_annual
            if sc.value_function == "s_curve":
                return self._vf_scurve(score, sc.scale_max_COP,
                                       sc.vf_inflection, sc.vf_steepness)
            return self._vf_linear(score, sc.scale_max_COP)

    def _calc_subcriterion(self, sc: SubCriterionInput, horizon: int = None) -> float:
        """COP de un sub-criterio. Ruta 1/2: físico × precio → NPV. Ruta 3: CR → VF → NPV."""
        if sc.type == "monetizable":
            if sc.price_per_unit is None:
                return 0.0
            annual_cop = sc.delta_annual * sc.price_per_unit
            return sc.weight * self._npv_annual(annual_cop, horizon=horizon)
        else:
            annual_cop = self._apply_vf(sc)
            return sc.weight * self._npv_annual(annual_cop, horizon=horizon)

    def _calc_driver(self, driver: DriverInput,
                     construction_years: int = 1,
                     benefit_start_offsets: dict = None,
                     horizon: int = None) -> dict:
        """
        Agrega sub-criterios de un driver y aplica el peso del driver (w_r).

        Ecuación: VD_r = w_r × Σ(SC_j) × FD_lag(driver)

        Temporalidad — mecanismo Copperleaf C55 §5.3 (correcto):
        ──────────────────────────────────────────────────────────
        La penalización por construcción larga es PURAMENTE FINANCIERA.
        El horizonte de análisis NO se reduce — los beneficios se proyectan
        sobre la vida útil del activo (que puede ser 25-40 años).

        FD_lag = 1 / (1 + WACC)^lag_total

        Donde lag_total = construction_years + benefit_start_offsets[driver.id]

        Interpretación: cada año de construcción representa capital inmovilizado
        sin retorno. El Factor de Descuento captura ese costo del tiempo.

        Diferencia clave vs. reducir el horizonte:
          Con WACC=10%, lag=3 años:
          - Reducir horizonte (INCORRECTO): ratio = Factor(7a)/Factor(10a) = 0.793
          - Descuento puro C55 (CORRECTO):  FD_lag = 1/(1.10)^3           = 0.751

        El mecanismo correcto penaliza más, lo cual es apropiado: el capital
        inmovilizado 3 años tiene un costo real mayor que simplemente "perder"
        3 años de beneficios en un horizonte finito.

        w_r = 1.0 por defecto. Valor distinto requiere aprobación ASPD.
        Ref: Copperleaf C55 VDM §3.2, §5.3.
        """
        if benefit_start_offsets is None:
            benefit_start_offsets = {}

        # Sub-criterios retirados: VD-1-F medía reputación dentro de VD-1;
        # su contenido se trasladó a VD-6 (Reto Reputación, agregado por
        # EPM). Usar VD-1-F duplicaría el aporte reputacional frente a
        # VD-6-A. El motor lo bloquea. Nota: ya NO es sobre la matriz de
        # riesgo — el componente reputacional se retiró de las anclas
        # L1-L5 al crear VD-6, así que esa duplicación específica ya no
        # aplica; la que sí aplica es VD-1-F vs. VD-6-A.
        _DEPRECATED_SC = {"VD-1-F"}

        sc_values = {}
        sc_total = 0.0
        _deprecated_skipped = []
        for sc in driver.sub_criteria:
            if sc.id in _DEPRECATED_SC:
                _deprecated_skipped.append(sc.id)
                sc_values[sc.id] = 0.0
                continue
            v = self._calc_subcriterion(sc, horizon=horizon)
            sc_values[sc.id] = v
            sc_total += v

        # w_r aplicado al nivel del driver (Copperleaf C55 §3.2)
        weighted_total = driver.weight * sc_total

        # ── Mecanismo correcto C55: descuento financiero puro por lag ─────────
        # El lag captura: (1) años de construcción + (2) rampa del driver
        extra_offset = benefit_start_offsets.get(driver.id, 0)
        lag_total = construction_years + extra_offset

        # FD_lag = 1/(1+WACC)^lag  — costo del tiempo, no pérdida de horizonte
        fd_lag = 1.0 / ((1.0 + self.wacc) ** lag_total) if lag_total > 0 else 1.0

        # Valor ajustado = beneficio_bruto × FD_lag
        # El horizonte completo se mantiene — solo se descuenta por el lag
        value_adjusted = weighted_total * fd_lag

        return {
            "id": driver.id,
            "reto": driver.reto,
            "weight_r": driver.weight,
            "sc_total_COP": sc_total,
            "value_COP": value_adjusted,          # Ajustado por temporalidad C55
            "value_COP_bruto": weighted_total,    # Sin ajuste (trazabilidad)
            "construction_years": construction_years,
            "benefit_lag_driver": extra_offset,
            "lag_total_years": lag_total,
            "fd_lag": fd_lag,                     # 1/(1+WACC)^lag — el factor real
            "sub_criteria_detail": sc_values,
            "deprecated_skipped": _deprecated_skipped,  # IDs ignorados por doble conteo
        }

    @staticmethod
    def _tasa_falla_anual(age: float, eta: float, beta: float) -> float:
        """Probabilidad de que el activo falle ESTE año, dado que llegó vivo.

            h(t) = (β/η) · (t/η)^(β−1)

        Es la magnitud correcta para el costo de riesgo, que es un gasto ANUAL:
        cuánto se espera perder por tener el activo un año más en servicio.

        No confundir con _pof_weibull_static(), que devuelve la probabilidad
        ACUMULADA — cuántos de una población instalada el mismo día ya fallaron.
        Esa mide estado de salud y se satura en 1: usarla como tasa anual
        equivale a afirmar que el activo falla con certeza todos los años, y
        cobrar cada año por fallas que ya ocurrieron. En un transformador de
        35 años la diferencia entre ambas es de 26 veces.

        Se acota a 1.0: con β > 1 la fórmula crece sin techo y para edades muy
        superiores a η devolvería una probabilidad mayor que la certeza.
        """
        if eta <= 0 or age <= 0 or beta <= 0:
            return 0.0
        return min(1.0, (beta / eta) * ((age / eta) ** (beta - 1.0)))

    @staticmethod
    def _pof_weibull_static(age: float, eta: float, beta: float) -> float:
        """PoF(t) = 1 − exp(−(age/η)^β)  [Weibull acumulada].

        Mide ESTADO DE SALUD: qué fracción de una población instalada el mismo
        día ya falló al llegar a esta edad. Para el costo de riesgo anual la
        magnitud correcta es _tasa_falla_anual().

        C55 — Curva de falla temporal basada en distribución Weibull.
        Reemplaza la aproximación exponencial (1+g)^t cuando se proporcionan
        parámetros físicos calibrados por clase de activo.

        Args:
            age:  Edad del activo en años (puede ser flotante).
            eta:  Vida característica η en años — 63.2% de activos habrán
                  fallado a esta edad (escala, siempre > 0).
            beta: Parámetro de forma β:
                    β < 1 → fallas tempranas (mortalidad infantil)
                    β = 1 → falla aleatoria (exp puro, hazard constante)
                    β > 1 → desgaste / envejecimiento acelerado
                  Rangos típicos EPM:
                    Transformadores:     β ≈ 2.5  η ≈ 40 a
                    Interruptores:       β ≈ 3.0  η ≈ 35 a
                    Cables MT:           β ≈ 2.2  η ≈ 30 a
                    Tuberías conducción: β ≈ 1.8  η ≈ 50 a
                    Bombas centrifugas:  β ≈ 3.5  η ≈ 20 a
                    SCADA/sistemas TI:   β ≈ 4.0  η ≈ 12 a
        Returns:
            PoF en [0, 1). Retorna 0.0 si parámetros inválidos.
        """
        if eta <= 0.0 or beta <= 0.0 or age < 0.0:
            return 0.0
        return 1.0 - math.exp(-((age / eta) ** beta))

    def _calc_risk(self, risk: RiskInput,
                   construction_years: int = 0,
                   horizon: int = None,
                   business_unit: str = "",
                   risk_growth_rate: float = 0.0,
                   deferral_years: int = 0) -> dict:
        """ΔR = (EAL_base − EAL_residual) × (1+g)^deferral × NPV_factor × FD_lag.

        Reglas que aplica (Copperleaf C55 §4.2-4.3):
        - FD_lag por años de obra: el riesgo solo baja tras comisionar (§5.3)
        - Horizonte dinámico por tipo de activo, no un valor fijo para todos
        - CoF con tabla propia por negocio y respaldo en la tabla genérica
        - Anualidad creciente de Gordon: modela la degradación del activo
        - Validación residual ≤ baseline: ΔR negativo con advertencia explícita
        - PoF independiente por tipo de consecuencia vía consequence_type_probs
        - Diferimiento: ΔR escala por (1+g)^d y FD_lag usa los años de obra ya
          extendidos por el diferimiento, MENOS la exposición sufrida esperando
        """
        rm = self.config.get("risk_matrix", {})
        h  = horizon if horizon is not None else self.horizon

        # ── CoF por BU con fallback a tabla genérica ─────────────────
        cof_by_bu = rm.get("consequence_levels_by_bu", {})
        cl = cof_by_bu.get(business_unit) or rm.get("consequence_levels", {})
        pl = rm.get("probability_levels", {})
        cof_source = business_unit if business_unit in cof_by_bu else "generic"
        ctm = rm.get("consequence_types_matrix", {})

        # ── Validación residual ≤ baseline ────────────────────────────
        warnings_list: list = []
        if (risk.residual_consequence_level > risk.baseline_consequence_level or
                risk.residual_probability_level > risk.baseline_probability_level):
            warnings_list.append(
                "⚠️ Riesgo residual supera el baseline — ΔR negativo (alternativa empeora "
                "el riesgo). Verificar niveles de Consecuencia y Probabilidad."
            )

        def _bu_levels_for_type(typ: str) -> dict:
            typ_data = ctm.get(typ, {})
            bu_levels = typ_data.get("levels_by_bu", {}).get(business_unit, {})
            if not bu_levels:
                all_bu = typ_data.get("levels_by_bu", {})
                bu_levels = next(iter(all_bu.values()), {}) if all_bu else {}
            return bu_levels

        def _cof_from_types(types_dict: dict) -> float:
            """Suma CoF multi-tipo con PoF compartido: Σ value_COP por tipo × nivel."""
            total = 0.0
            for typ, lvl in types_dict.items():
                total += _bu_levels_for_type(typ).get(str(lvl), {}).get("value_COP", 0)
            return total

        def _eal_from_types_per_prob(types_dict: dict, type_probs_dict: dict,
                                     shared_prob_level: int) -> float:
            """EAL multi-tipo con PoF por tipo — C55: Σ(CoF_tipo × PoF_tipo)."""
            total = 0.0
            for typ, lvl in types_dict.items():
                cof = _bu_levels_for_type(typ).get(str(lvl), {}).get("value_COP", 0)
                prob_lvl = type_probs_dict.get(typ, shared_prob_level)
                pof = pl.get(str(prob_lvl), {}).get("annual_prob", 0)
                total += cof * pof
            return total

        def risk_annual(cons_level: int, prob_level: int,
                        types_dict: dict = None, type_probs_dict: dict = None) -> float:
            if types_dict and type_probs_dict:
                # C55: PoF independiente por tipo
                return _eal_from_types_per_prob(types_dict, type_probs_dict, prob_level)
            elif types_dict:
                # Multi-tipo CoF con PoF compartido (comportamiento anterior)
                c = _cof_from_types(types_dict)
                p = pl.get(str(prob_level), {}).get("annual_prob", 0)
                return c * p
            else:
                c = cl.get(str(cons_level), {}).get("value_COP", 0)
                p = pl.get(str(prob_level), {}).get("annual_prob", 0)
                return c * p

        base     = risk_annual(risk.baseline_consequence_level,
                               risk.baseline_probability_level,
                               risk.consequence_types or None,
                               risk.consequence_type_probs or None)
        residual = risk_annual(risk.residual_consequence_level,
                               risk.residual_probability_level,
                               risk.residual_consequence_types or None,
                               risk.residual_consequence_type_probs or None)
        delta_r  = base - residual

        # ── Modo directo ──────────────────────────────────────────────────────
        # El analista ya conoce el costo anual en pesos y no necesita derivarlo
        # de la matriz. Reemplaza a AMBOS lados para que la resta sea coherente:
        # mezclar una base monetizada con un residual de matriz produciría un ΔR
        # sin significado.
        modo_directo = getattr(risk, "baseline_annual_COP", 0.0) > 0
        if modo_directo:
            base     = float(risk.baseline_annual_COP)
            residual = float(getattr(risk, "residual_annual_COP", 0.0) or 0.0)
            delta_r  = base - residual
            cof_source = "monto_directo"

        # ── C55: Weibull PoF temporal (RF9) ─────────────────────────
        # Si el RiskInput incluye parámetros Weibull calibrados, se reemplaza
        # la aproximación exponencial (1+g)^t por la curva PoF(age+t).
        # Lógica:
        #   CoF se extrae de la matriz de consecuencia (existente, sin cambio).
        #   PoF_baseline(t) = Weibull(age + deferral,  η, β)
        #   PoF_residual(t) = PoF_baseline(t) × ratio_prob  ← ratio de la matriz 1-5
        #   ΔR_annual(t)    = CoF_base × PoF_b − CoF_res × PoF_r
        #   ΔR_NPV          = Σ_{k=1}^{H} ΔR_annual(age+deferral+k) / (1+r)^k
        # Si weibull_beta == 0 se usa la rama exponencial: no es código muerto,
        # es el camino para activos sin curva de falla calibrada.
        pof_mode   = "exponential"
        npv_factor = None  # redefinido más abajo según rama
        pof_baseline_at_eval = None
        pof_residual_at_eval = None
        exposure_npv = 0.0  # ΔR que se deja de capturar mientras se difiere

        fd_lag = 1.0 / ((1.0 + self.wacc) ** construction_years) if construction_years > 0 else 1.0

        # El modo directo fuerza la rama exponencial: un costo anual conocido que
        # crece a una tasa NO es una curva de envejecimiento, y evaluarlo con
        # Weibull exigiría una edad y una vida característica que en una
        # expansión no existen.
        if (not modo_directo) and risk.weibull_beta > 0 and risk.weibull_eta > 0:
            # ── Rama Weibull ─────────────────────────────────────────────────
            pof_mode = "weibull"
            age_eval = risk.asset_age_years + deferral_years  # edad en el momento de evaluación

            # CoF base: extraer valor monetario sin multiplicar por PoF
            # (reutiliza misma jerarquía multi-tipo que risk_annual() pero solo CoF)
            def _cof_base_only() -> float:
                if risk.consequence_types and risk.consequence_type_probs:
                    return sum(
                        _bu_levels_for_type(typ).get(str(lvl), {}).get("value_COP", 0)
                        for typ, lvl in risk.consequence_types.items()
                    )
                elif risk.consequence_types:
                    return _cof_from_types(risk.consequence_types)
                return cl.get(str(risk.baseline_consequence_level), {}).get("value_COP", 0)

            def _cof_residual_only() -> float:
                if risk.residual_consequence_types and risk.residual_consequence_type_probs:
                    return sum(
                        _bu_levels_for_type(typ).get(str(lvl), {}).get("value_COP", 0)
                        for typ, lvl in risk.residual_consequence_types.items()
                    )
                elif risk.residual_consequence_types:
                    return _cof_from_types(risk.residual_consequence_types)
                return cl.get(str(risk.residual_consequence_level), {}).get("value_COP", 0)

            cof_b = _cof_base_only()
            cof_r = _cof_residual_only()

            # Ratio probabilidad: escala PoF residual desde la curva base.
            # El nivel de probabilidad de la matriz (1-5) informa cuánto mejora
            # la intervención la confiabilidad → PoF_res = PoF_base × ratio.
            pof_matrix_b = pl.get(str(risk.baseline_probability_level),  {}).get("annual_prob", 1e-9)
            pof_matrix_r = pl.get(str(risk.residual_probability_level),  {}).get("annual_prob", 0.0)
            ratio_prob   = (pof_matrix_r / pof_matrix_b) if pof_matrix_b > 1e-12 else 0.0

            # Estado de salud en el momento de evaluación (acumulada, diagnóstico)
            pof_baseline_at_eval = self._pof_weibull_static(
                age_eval, risk.weibull_eta, risk.weibull_beta)
            pof_residual_at_eval = pof_baseline_at_eval * ratio_prob

            # Y la tasa ANUAL, que es la que se convierte en dinero
            h_b0 = self._tasa_falla_anual(age_eval, risk.weibull_eta, risk.weibull_beta)
            h_r0 = h_b0 * ratio_prob

            # ΔR anual en el punto de evaluación
            delta_r = cof_b * h_b0 - cof_r * h_r0

            # ΔR NPV: integración discreta H años post-intervención.
            # El activo continúa envejeciendo; sin intervención seguiría la curva base;
            # con intervención, el PoF efectivo permanece escalado por ratio_prob
            # (equivalente a "renovación parcial" que extiende la vida útil).
            delta_r_npv_pre_lag = 0.0
            for k in range(1, h + 1):
                age_k  = age_eval + k
                h_bk   = self._tasa_falla_anual(age_k, risk.weibull_eta, risk.weibull_beta)
                h_rk   = h_bk * ratio_prob
                dr_k   = cof_b * h_bk - cof_r * h_rk
                delta_r_npv_pre_lag += dr_k / ((1.0 + self.wacc) ** k)

            delta_r_npv = delta_r_npv_pre_lag * fd_lag

            # Exposición durante la espera (rama Weibull): los años que se
            # difiere se vive con la curva base, no con la residual.
            if deferral_years > 0:
                for t in range(1, deferral_years + 1):
                    h_bt = self._tasa_falla_anual(
                        risk.asset_age_years + t, risk.weibull_eta, risk.weibull_beta)
                    dr_t = cof_b * h_bt - cof_r * (h_bt * ratio_prob)
                    exposure_npv += dr_t / ((1.0 + self.wacc) ** t)

        else:
            # ── Rama exponencial (C55) ──────────────────────────────
            # Exposición durante la espera: el ΔR que se deja de capturar en
            # los años que se posterga. Sin este término, diferir solo tenía
            # premio — el riesgo crecía por (1+g)^d y nadie cobraba el haber
            # convivido con él. Con g > r en los cuatro negocios, eso hacía
            # que el VNT creciera indefinidamente y el año óptimo cayera
            # siempre en el borde de la ventana de análisis.
            if deferral_years > 0:
                for t in range(1, deferral_years + 1):
                    dr_t = delta_r * ((1.0 + risk_growth_rate) ** t)
                    exposure_npv += dr_t / ((1.0 + self.wacc) ** t)

            # Escalar ΔR por crecimiento acumulado del riesgo
            if deferral_years > 0 and risk_growth_rate > 0:
                delta_r *= (1.0 + risk_growth_rate) ** deferral_years

            # Anualidad creciente Gordon
            # ΔR_NPV = ΔR_anual × (1 − ((1+g)/(1+r))^H) / (r − g)
            r = self.wacc
            g = risk_growth_rate
            if abs(r - g) < 1e-9:
                npv_factor = float(h)
            elif g > 0:
                npv_factor = (1.0 - ((1.0 + g) / (1.0 + r)) ** h) / (r - g)
            else:
                npv_factor = self._calc_factor_npv(h)

            delta_r_npv_pre_lag = delta_r * npv_factor
            delta_r_npv         = delta_r_npv_pre_lag * fd_lag

        # El riesgo neto de diferir = el mitigado a futuro MENOS el que se
        # sufrió esperando. Sin restar, postergar era gratis.
        delta_r_npv -= exposure_npv

        return {
            "baseline_annual_COP":   base,
            "residual_annual_COP":   residual,
            "delta_r_annual_COP":    delta_r,
            "delta_r_npv_COP":       delta_r_npv,
            "deferral_exposure_COP": exposure_npv,
            "npv_factor_used":       npv_factor,        # None en modo Weibull (discreta)
            "fd_lag":                fd_lag,
            "horizon_used":          h,
            "risk_growth_rate_used": risk_growth_rate if pof_mode == "exponential" else 0.0,
            "cof_source":            cof_source,
            "cof_mode":              ("per_type_pof" if risk.consequence_type_probs else "multi_type") if risk.consequence_types else "single_level",
            "pof_mode":              pof_mode,           # "weibull" | "exponential"
            "weibull_beta":          risk.weibull_beta,
            "weibull_eta":           risk.weibull_eta,
            "asset_age_years":       risk.asset_age_years,
            "pof_baseline_at_eval":  pof_baseline_at_eval,   # acumulada — estado de salud
            "tasa_falla_anual_base": (h_b0 if pof_mode == "weibull" else None),
            "pof_residual_at_eval":  pof_residual_at_eval,   # None si exponencial
            "deferral_years":        deferral_years,
            "warnings":              warnings_list,
        }

    def _get_weibull_params_for_asset(self, asset_type: str) -> tuple[float, float] | None:
        """Devuelve (beta, eta) Weibull desde DEFAULT_CONFIG para el tipo de activo.

        C55 — Lookup de parámetros Weibull calibrados por clase de activo.
        Retorna None si el tipo no tiene parámetros configurados (usar exponencial).

        Uso sugerido (en evaluate() o ProjectWizard):
            params = engine._get_weibull_params_for_asset(project.asset_type)
            if params:
                project.risk.weibull_beta, project.risk.weibull_eta = params
        """
        ot = self.config.get("optimal_timing", {})
        wp = ot.get("weibull_params_by_asset", {})
        if asset_type and asset_type in wp:
            entry = wp[asset_type]
            if isinstance(entry, dict):
                beta = float(entry.get("beta", 0))
                eta  = float(entry.get("eta",  0))
                if beta > 0 and eta > 0:
                    return beta, eta
        return None

    def _get_risk_growth_rate(self, business_unit: str, asset_type: str = "") -> float:
        """Tasa de degradación anual del riesgo.

        Jerarquía de lookup:
          1. risk_growth_rate_by_asset[asset_type]  — más específico (β Weibull)
          2. risk_growth_rate_by_bu[business_unit]  — por negocio
          3. risk_growth_rate global                — fallback (default 15%)

        Nota: cuando RiskInput.weibull_beta > 0, este valor ya no se usa
        en _calc_risk() — el motor usa la curva Weibull completa en su lugar.
        Se mantiene para backward compatibility con proyectos sin parámetros Weibull.
        """
        ot = self.config.get("optimal_timing", {})
        by_asset = ot.get("risk_growth_rate_by_asset", {})
        if asset_type and isinstance(by_asset, dict) and asset_type in by_asset:
            return float(by_asset[asset_type])
        by_bu = ot.get("risk_growth_rate_by_bu", {})
        if isinstance(by_bu, dict) and business_unit in by_bu:
            return float(by_bu[business_unit])
        return float(ot.get("risk_growth_rate", 0.15))

    def _calc_timing(self, project: ProjectInput,
                     benefits_npv: float) -> dict:
        """
        Cost of Delay (CoD) — Copperleaf C55 §6.2.

        El CoD mide el valor mensual que EPM pierde por ejecutar el proyecto
        DESPUÉS del año óptimo. Es el único mecanismo de penalización por
        retraso en la decisión — no existe TF multiplicador sobre beneficios.

        CoD_mensual = Beneficios_NPV / Horizonte_meses
        CoD_total   = CoD_mensual × meses_de_retraso

        NOTA: El ΔR es un componente INDEPENDIENTE del VNT (Copperleaf C55 §4.3).
        No entra en el cálculo del CoD para evitar doble conteo.
        """
        delay = max(0, project.execution_year - project.optimal_year)
        # Issue 9: horizonte por tipo de activo — un transformador (30a) tiene CoD
        # mensual 3× menor que si se divide por el horizonte global de 10a.
        # Usar el horizonte incorrecto sobreestima la urgencia para activos de larga vida.
        h_asset = self.get_horizon_for_asset(project)
        horizon_months = h_asset * 12
        # CoD sobre beneficios de drivers únicamente — ΔR separado
        cod_monthly = benefits_npv / horizon_months if horizon_months > 0 else 0.0
        cod_total = cod_monthly * delay * 12 if delay > 0 else 0.0
        return {
            "delay_years": delay,
            "horizon_asset_years": h_asset,
            "cod_monthly_COP": cod_monthly,
            "cod_total_COP": cod_total
        }

    def evaluate(self, project: ProjectInput, deferral_years: int = 0) -> dict:
        """
        Evaluación completa de un proyecto.
        Retorna Net_Value y VEI como métricas principales.

        deferral_years: años de postergación respecto al año base del plan.
          - Aumenta FD_lag en drivers y riesgo (más descuento por espera)
          - Escala ΔR_annual por (1+g)^deferral (el riesgo creció durante la espera)
          - Infla CAPEX por tasa de inflación de obra civil
        """
        # ── WACC por BU: activar durante toda la evaluación ──────────────────
        _orig_wacc = self.wacc
        self.wacc = self._get_wacc(project.business_unit)
        try:
            return self._evaluate_inner(project, deferral_years)
        finally:
            self.wacc = _orig_wacc

    def _evaluate_inner(self, project: "ProjectInput", deferral_years: int = 0) -> dict:
        """Núcleo de evaluate() — llamado con self.wacc ya fijado al WACC de la BU."""
        # ── Effective construction years para FD_lag ───────────────
        eff_cy = project.construction_years + deferral_years

        # 1. Beneficios ajustados por temporalidad (construction_years + benefit_start_offsets)
        #    Cada driver puede tener un lag diferente según configura el analista.
        #    Ref: Copperleaf C55 §5.3 — Execution Duration & Benefit Timing.
        h_asset = self.get_horizon_for_asset(project)

        # Ventana de evaluación COMÚN. Sin esto, diferir d años seguía dando
        # los mismos h años de beneficios, solo que empezando más tarde: cada
        # alternativa de fecha se evaluaba sobre una ventana distinta y la que
        # empezaba después miraba más lejos en el futuro. Con el riesgo
        # creciendo más rápido que el WACC (g > r en los cuatro negocios), esa
        # ventana corrida bastaba para que diferir siempre ganara y el año
        # óptimo cayera en el borde. Comparar alternativas mutuamente
        # excluyentes exige el mismo horizonte de calendario para todas.
        h_eval = max(1, h_asset - deferral_years) if deferral_years > 0 else h_asset

        driver_results = [
            self._calc_driver(
                d,
                construction_years=eff_cy,
                benefit_start_offsets=project.benefit_start_offsets,
                horizon=h_eval,
            )
            for d in project.drivers
        ]
        benefits_raw = sum(d["value_COP"] for d in driver_results)
        benefits_bruto = sum(d["value_COP_bruto"] for d in driver_results)

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

        # 3. Cost of Delay — Copperleaf C55 §6.2
        #    Solo depende de los beneficios de los drivers y el retraso.
        #    ΔR no entra aquí — es un componente independiente del VNT.
        timing = self._calc_timing(project, benefits_raw)
        cod = timing["cod_total_COP"]

        # 4. Costos (CAPEX + OPEX incremental como medidas negativas)
        capex_inflation_rate = self.config.get("capex_inflation_rate", 0.04)

        if project.costs.capex_by_year:
            # Perfil explícito por año calendario, pesos nominales ya
            # inflados por el analista — el motor solo descuenta, no infla.
            #
            # offset_k = año_k − declared_execution_year: la FORMA del
            # perfil, fija desde que se declaró. Cada tramo se descuenta a
            # (offset_k + deferral_years) años desde hoy.
            #
            # El desplazamiento temporal usa deferral_years, EXACTAMENTE
            # como el CAPEX legado (rama else) y como los otros tres
            # componentes de la ecuación (beneficios, riesgo, CoD) — es la
            # única referencia temporal compartida de toda la evaluación.
            # execution_year NO participa en esta fórmula.
            #
            # (Corregido tras revisión de calidad: la primera versión usaba
            # delta = execution_year − declared_execution_year en vez de
            # deferral_years. EnterpriseOptimizer reasigna execution_year AL
            # CANDIDATO Y ADEMÁS pasa su propio deferral_years — dos
            # referencias que solo coinciden por casualidad. Un proyecto
            # declarado en 2029 evaluado como candidato en el año base del
            # plan (deferral_years=0) daba capex_pv 31% por encima del
            # nominal con la versión anterior.)
            declared = project.declared_execution_year or project.execution_year
            capex = sum(project.costs.capex_by_year.values())
            # NOTA DE AUDITORÍA (hallada en revisión de calidad): las claves
            # de capex_pv_por_tramo son los años calendario TAL COMO SE
            # DECLARARON — no el año en que el tramo aterriza en el
            # escenario que se está evaluando. _evaluate_inner() no conoce
            # el año base del plan (solo deferral_years, un conteo de años,
            # no un año calendario), así que no puede calcular un "año de
            # aterrizaje" absoluto aquí dentro. Léase como "el valor
            # presente del tramo que el analista declaró para el año X",
            # no como "lo que este tramo cuesta en el año X de este
            # escenario" — la rama legada (abajo) sí usa un año de
            # escenario real (execution_year) como clave, así que las dos
            # ramas no son directamente comparables por la clave.
            capex_pv_por_tramo = {
                year: monto / ((1.0 + self.wacc) ** ((year - declared) + deferral_years))
                for year, monto in project.costs.capex_by_year.items()
            }
            capex_pv = sum(capex_pv_por_tramo.values())
        else:
            # Camino legado — sin cambios de comportamiento.
            capex_base = project.costs.capex_COP
            capex = capex_base * ((1.0 + capex_inflation_rate) ** deferral_years) if deferral_years > 0 else capex_base
            # CAPEX a valor presente. Se inflaba a pesos del año de ejecución y se
            # restaba contra beneficios en pesos de hoy, sin descontarlo de vuelta:
            # a 5 años de diferimiento el motor le cobraba al proyecto un 57% más
            # de lo que ese desembolso vale hoy. `capex` (nominal) se conserva para
            # los umbrales de proporcionalidad, que miden tamaño de obra, no valor.
            capex_pv = capex
            if deferral_years > 0:
                capex_pv = capex / ((1.0 + self.wacc) ** deferral_years)
            capex_pv_por_tramo = {project.execution_year: capex_pv}

        # OPEX incremental: anualidad plana, sin rezago y sin crecimiento.
        # Dos decisiones abiertas pendientes de ASPD, medidas y documentadas en
        # el registro de asuntos abiertos: (1) si debe arrastrar el mismo rezago
        # que los beneficios, dado que la operación empieza cuando el activo
        # entra en servicio; (2) si debe crecer a la tasa que ya declara
        # degradation_engine.opex_growth_rate, que hoy solo usa el motor de
        # degradación. Cualquiera de las dos altera la conformidad con C55.
        opex_npv = self._npv_annual(project.costs.opex_incremental_COP_year,
                                    horizon=h_asset)

        cost_npv = project.costs.weight * (capex_pv + opex_npv)  # weight = -1.0

        # 5. Ecuación maestra C55 — fiel a Copperleaf VDM §3.1
        #
        #    VNT = Σ(w_r × VD_r × FD_lag)  −  CoD  +  ΔR  −  CAPEX  −  ΔOPEX
        #
        #    Tres componentes INDEPENDIENTES:
        #      benefits_raw : beneficios descontados por FD_lag (temporalidad)
        #      cod          : penalización por retraso vs. año óptimo
        #      delta_r_npv  : riesgo mitigado en NPV
        #
        #    NO hay TF multiplicando los beneficios — ese mecanismo duplicaba
        #    el castigo por retraso que el CoD ya captura correctamente.
        net_value = benefits_raw - cod + delta_r_npv + cost_npv

        # 6. VEI clásico + VEI_EAV normalizado por horizonte de activo
        # Denominador en valor presente, igual que el numerador: dividir un VNT
        # descontado entre un CAPEX en pesos futuros mezclaba dos monedas.
        vei = net_value / capex_pv if capex_pv > 0 else 0.0

        # Horizonte dinámico por tipo de activo
        # Permite EAV justo para comparar activos de larga vida vs. tecnología
        horizon_asset = self.get_horizon_for_asset(project)
        eav_cop = self.calc_eav(net_value, horizon_asset)
        # VEI_EAV = EAV / (CAPEX/H) — eficiencia anualizada, comparable entre tipos
        vei_eav = eav_cop / (capex_pv / horizon_asset) if capex_pv > 0 and horizon_asset > 0 else 0.0

        # 7. Nivel de modelo (proporcionalidad IAM SSG)
        model_level = self._get_model_level(capex, project.business_unit)

        # 8. Alerta pedagógica si VNT < 0
        alerta_vnt_negativo = None
        if net_value < 0:
            _alt_delay = max(0, project.execution_year - project.optimal_year)
            if _alt_delay == 0:
                alerta_vnt_negativo = (
                    f"⚠️ Inversión destructora de valor incluso en el momento óptimo: "
                    f"VNT = {net_value/1e6:,.1f} MM COP. "
                    "El activo ya alcanzó su punto crítico — posponer agrava la pérdida. "
                    "Considera: (a) reducir el CAPEX o el alcance, "
                    "(b) ampliar los beneficios monetizables del proyecto."
                )
            else:
                alerta_vnt_negativo = (
                    f"⚠️ Inversión destructora de valor: El costo supera el riesgo de la inacción. "
                    f"VNT = {net_value/1e6:,.1f} MM COP. "
                    "Considera: (a) revisar la magnitud de los beneficios estimados, "
                    "(b) reducir el CAPEX o el alcance, (c) esperar al año óptimo de "
                    "intervención cuando el deterioro justifique la inversión."
                )

        # 9. Badge metodológico — rigor del dato
        methodology = getattr(project, "valuation_methodology", "driver_based")
        _badge_map = {
            "concept_rating": ("🟡 Concept Rating", "Cualitativo · Escala 0-10"),
            "driver_based":   ("🟢 Driver-Based",   "Métrico · Precio regulatorio"),
            "asset_aware":    ("🔵 Asset-Aware",    "Predictivo · Weibull/EEOL"),
        }
        badge_label, badge_desc = _badge_map.get(
            methodology, ("⚪ No definido", "Sin clasificar"))

        return {
            "project_id": project.id,
            "project_name": project.name,
            "business_unit": project.business_unit,
            "investment_type": project.investment_type,
            "asset_type": getattr(project, "asset_type", ""),
            "is_baseline": getattr(project, "is_baseline", False),
            "exclusion_group_id": getattr(project, "exclusion_group_id", None),
            "valuation_methodology": methodology,
            "badge_label": badge_label,
            "badge_desc": badge_desc,
            "model_level": model_level,
            "capex_COP": capex,                 # nominal del año de ejecución
            "capex_pv_COP": capex_pv,           # el que entra a la ecuación
            "capex_schedule_pv_COP": capex_pv_por_tramo,  # cada tramo, para auditoría
            "opex_incremental_npv_COP": opex_npv,
            "analysis_horizon_years": self.horizon,
            "horizon_asset_years": horizon_asset,
            "wacc": self.wacc,
            "factor_npv": self._factor_npv,
            "driver_results": driver_results,
            "benefits_raw_COP": benefits_raw,
            "benefits_cod_ajustado_COP": benefits_raw - cod,
            "risk_analysis": risk_result,
            "delta_r_npv_COP": delta_r_npv,
            "timing_analysis": timing,
            "cost_npv_COP": cost_npv,
            "net_value_COP": net_value,
            "value_efficiency_index": vei,
            # ── EAV y VEI normalizado ────────────────────────────────────
            "eav_COP": eav_cop,
            "vei_eav": vei_eav,
            "alerta_vnt_negativo": alerta_vnt_negativo,
            "is_mandatory": project.is_mandatory,
            # ── Temporalidad ───────────────────────────────────────────────────
            "construction_years": project.construction_years,
            "benefit_start_offsets": project.benefit_start_offsets,
            "benefits_bruto_COP": benefits_bruto,
            "benefits_ajustados_COP": benefits_raw,
            "penalizacion_temporalidad_COP": benefits_bruto - benefits_raw,
            "evaluation_timestamp": datetime.now().isoformat(),
            "optimal_timing": None   # Calculado bajo demanda con evaluate_with_timing()
        }

    # ══════════════════════════════════════════════════════════════════════════
    # INCERTIDUMBRE DE ESTIMACIÓN — banda de VNT por alternativa
    # ══════════════════════════════════════════════════════════════════════════

    def get_estimate_bands(self, business_unit: str = "") -> dict:
        """Bandas de desviación del CAPEX vigentes para un negocio.

        Jerarquía: classes_by_bu[BU] > classes (referencia de industria).
        """
        cfg = self.config.get("cost_estimate_uncertainty", {})
        by_bu = cfg.get("classes_by_bu", {}) or {}
        if business_unit and business_unit in by_bu:
            return by_bu[business_unit]
        return cfg.get("classes", {})

    def estimate_band(self, project: "ProjectInput",
                      deferral_years: int = 0,
                      base_result: dict = None) -> dict:
        """Banda de VNT que produce la incertidumbre de estimación del CAPEX.

        Trata UNA sola incertidumbre: qué tan bien se conoce el costo de ESTA
        alternativa (CostInput.estimate_class). No toca el riesgo ni los
        beneficios — la incertidumbre de condición del activo ya la trata el
        Weibull en _calc_risk, y duplicarla aquí sería doble conteo.

        Cálculo exacto, sin re-evaluar: la ecuación maestra implementada es

            VNT = benefits_raw − CoD + ΔR_npv − CAPEX_VP − ΔOPEX_npv

        donde el CAPEX entra con coeficiente −1.0 (costs.weight). Por tanto

            VNT(CAPEX × k) = VNT(CAPEX) − (k − 1) × CAPEX

        es exacta y no requiere volver a correr los drivers ni el riesgo.

        Nota de dirección: CAPEX alto → VNT bajo. net_value_low corresponde al
        extremo pesimista de costo.

        Args:
            project:       Alternativa a evaluar.
            deferral_years: Años de postergación (para usar el CAPEX inflado).
            base_result:   Resultado de evaluate() si ya se calculó (evita recalcular).

        Returns:
            dict con la clase, su etiqueta, los extremos de CAPEX y de VNT.
        """
        if base_result is None:
            base_result = self.evaluate(project, deferral_years=deferral_years)

        cfg      = self.config.get("cost_estimate_uncertainty", {})
        bands    = self.get_estimate_bands(project.business_unit)
        declared = getattr(project.costs, "estimate_class", None)
        default  = cfg.get("default_class", "Class5")
        cls_key  = declared if declared in bands else default
        band     = bands.get(cls_key) or bands.get("Class5") or {"low": -0.30, "high": +0.50}

        # El CAPEX tal como ENTRA a la ecuación: inflado por la postergación y
        # traído a valor presente. Usar el nominal aquí ensanchaba la banda,
        # porque desplazaba un VNT descontado con pesos del año de ejecución.
        capex_eff = (base_result.get("capex_pv_COP")
                     or base_result.get("capex_COP")
                     or project.costs.capex_COP) or 0.0
        nv_base   = base_result.get("net_value_COP", 0.0)

        f_low, f_high = float(band.get("low", 0.0)), float(band.get("high", 0.0))
        capex_low  = capex_eff * (1.0 + f_low)    # costo optimista
        capex_high = capex_eff * (1.0 + f_high)   # costo pesimista

        # Coeficiente −1.0 del CAPEX en la ecuación maestra
        nv_high = nv_base - (capex_low  - capex_eff)   # costo bajo  → VNT alto
        nv_low  = nv_base - (capex_high - capex_eff)   # costo alto  → VNT bajo

        return {
            "estimate_class":    cls_key,
            "estimate_declared": declared,
            "is_default":        cls_key != declared,
            "label":             band.get("label", cls_key),
            "description":       band.get("description", ""),
            "factor_low":        f_low,
            "factor_high":       f_high,
            "capex_base_COP":    capex_eff,
            "capex_low_COP":     capex_low,
            "capex_high_COP":    capex_high,
            "net_value_base_COP": nv_base,
            "net_value_low_COP":  nv_low,
            "net_value_high_COP": nv_high,
            "band_width_COP":     nv_high - nv_low,
            "calibrated_with_epm_data": bool(cfg.get("_calibrado_con_datos_epm", False)),
        }

    @staticmethod
    def overlap_fraction(band_a: dict, band_b: dict) -> float:
        """Fracción de traslape entre dos bandas de VNT.

        Devuelve el solape como fracción del ancho de la banda MÁS ANGOSTA —
        así un traslape total marca 1.0 aunque una banda sea mucho más ancha
        que la otra. 0.0 = bandas disjuntas (la comparación está resuelta).
        """
        lo_a, hi_a = band_a["net_value_low_COP"], band_a["net_value_high_COP"]
        lo_b, hi_b = band_b["net_value_low_COP"], band_b["net_value_high_COP"]

        overlap = min(hi_a, hi_b) - max(lo_a, lo_b)
        if overlap <= 0:
            return 0.0
        narrowest = min(hi_a - lo_a, hi_b - lo_b)
        if narrowest <= 0:
            return 1.0
        return min(1.0, overlap / narrowest)

    def optimal_timing_analysis(self, project: "ProjectInput",
                                 horizon_years: int = 5,
                                 risk_growth_rate: float = None,
                                 capex_inflation_rate: float = 0.04) -> dict:
        """
        Análisis de Tiempo Óptimo de Intervención — Copperleaf Economic End-of-Life.

        Evalúa el VNT para t=0, t+1, ..., t+horizon_years simulando:
          - Degradación del riesgo: ΔR crece exponencialmente con risk_growth_rate
            (activo envejece → mayor probabilidad de falla → mayor riesgo base)
          - Inflación del CAPEX: costo aumenta por año de postergación
            (emergencias, escalación de materiales, ventanas de oportunidad perdidas)
          - Factor de descuento WACC: valor presente disminuye con el tiempo

        El año con el VNT más alto es el Tiempo Óptimo de Intervención.

        Parámetros calibrables (config.json):
          optimal_timing.horizon_years         (default 5 — vigencias a evaluar)
          optimal_timing.risk_growth_rate      (default 15%/año — degradación típica activo eléctrico)
          optimal_timing.capex_inflation_rate  (default 4%/año — inflación + sobrecosto emergencia)

        Referencia metodológica: Copperleaf "Managing Risk and Value" Fig. 8-9
        """
        import copy

        # ── WACC y horizonte por BU para toda la simulación de timing ───────
        _orig_wacc_ota = self.wacc
        self.wacc = self._get_wacc(project.business_unit)
        # Resolver horizon_years por BU antes de llamar al inner
        # (el parámetro de función es fallback si el llamador lo pasa explícito;
        #  _get_timing_horizon() lo sobrescribe con la jerarquía by_bu > global)
        horizon_years = self._get_timing_horizon(project.business_unit)
        try:
            return self._optimal_timing_inner(
                project, horizon_years, risk_growth_rate, capex_inflation_rate
            )
        finally:
            self.wacc = _orig_wacc_ota

    def _optimal_timing_inner(self, project: "ProjectInput",
                               horizon_years: int = 5,
                               risk_growth_rate: float = None,
                               capex_inflation_rate: float = 0.04) -> dict:
        """Núcleo de optimal_timing_analysis() — self.wacc ya fijado al WACC BU."""
        import copy

        # Leer TODOS los parámetros del config primero.
        # horizon_years se resuelve con jerarquía: by_bu[BU] > global config > arg.
        # Cuando se llama desde optimal_timing_analysis(), ya viene resuelto;
        # cuando se llama directamente, se resuelve aquí como fallback seguro.
        ot_cfg = self.config.get("optimal_timing", {})
        horizon_years        = self._get_timing_horizon(project.business_unit)
        # Issue 7b: propagar asset_type al lookup de risk_growth_rate
        risk_growth_rate     = self._get_risk_growth_rate(
            project.business_unit,
            asset_type=getattr(project, "asset_type", ""),
        )
        capex_inflation_rate = ot_cfg.get("capex_inflation_rate", capex_inflation_rate)

        base_year  = project.optimal_year
        capex_base = project.costs.capex_COP

        # Issue 8: horizonte del activo para NPV del ΔR en cada año t
        h_asset_timing = self.get_horizon_for_asset(project)

        # Issue 2b: lag fijo de construcción — se aplica igual que en evaluate().
        # En timing, tf cubre el delay de la decisión; fd_constr cubre el tiempo
        # de obra. Ambos son independientes y se multiplican.
        fd_constr = (1.0 / ((1.0 + self.wacc) ** project.construction_years)
                     if project.construction_years > 0 else 1.0)

        yearly_results = []

        for t in range(horizon_years + 1):
            year = base_year + t

            # ── Descuento por el retraso de la decisión ──────────────────────
            # Se calcula ANTES del bloque de riesgo porque cada rama lo necesita
            # para devolver su ΔR ya completamente descontado.
            tf = 1.0 / ((1 + self.wacc) ** t) if t > 0 else 1.0

            # ── CAPEX ajustado por inflación/emergencia ──────────────────────
            # capex_t es NOMINAL: lo que costará la obra en pesos del año t.
            capex_t = capex_base * ((1 + capex_inflation_rate) ** t)
            # capex_pv_t es el que entra a la ecuación, igual que en evaluate().
            # Restar un desembolso inflado a pesos futuros contra beneficios en
            # pesos de hoy mezcla dos monedas y hace que diferir parezca siempre
            # más caro de lo que es: con inflación de obra (4%) por debajo del
            # WACC (9,5%), esperar un año ABARATA el desembolso en valor
            # presente, y sin este descuento el análisis nunca podía verlo.
            # Ref. Copperleaf "Managing Risk and Value" §4.4: "the discount rate
            # will drive the project cost down into the future".
            capex_pv_t = capex_t * tf

            # ── Riesgo base creciente por degradación exponencial ───────────
            # El activo se degrada: ΔR_base_anual crece t% compuesto por año
            modified_project = copy.deepcopy(project)
            modified_project.execution_year = year
            modified_project.optimal_year   = base_year
            modified_project.costs = copy.deepcopy(project.costs)
            modified_project.costs.capex_COP = capex_t

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

            # ── Beneficios de los drivers (constantes — la inversión no cambia) ──
            driver_results = [
                self._calc_driver(
                    d,
                    construction_years=modified_project.construction_years,
                    benefit_start_offsets=modified_project.benefit_start_offsets,
                    horizon=h_asset_timing,
                )
                for d in modified_project.drivers
            ]
            benefits_raw = sum(d["value_COP"] for d in driver_results)

            # ── CoD acumulado por postergación ───────────────────────────────
            # CoD = valor de beneficios de drivers perdido cada mes por no ejecutar.
            # ΔR degradado NO se incluye en cod_monthly — entra explícito en
            # benefits_timed para reflejar el crecimiento del riesgo año a año
            # sin doble conteo (era el bug previo: se sumaba en CoD y en ΔR).
            horizon_months = h_asset_timing * 12
            cod_monthly = benefits_raw / horizon_months if horizon_months > 0 else 0.0
            cod_total = cod_monthly * (t * 12) if t > 0 else 0.0

            # ── Net Value en este año ────────────────────────────────────────
            opex_npv = self._npv_annual(project.costs.opex_incremental_COP_year, horizon=h_asset_timing)
            cost_npv_t = -1.0 * (capex_pv_t + opex_npv)

            # Cada rama de riesgo ya devolvió su ΔR COMPLETAMENTE descontado
            # (por retraso de la decisión y por años de obra), de modo que aquí
            # solo se suma. Antes se volvía a multiplicar por (tf × fd_constr),
            # lo que en la rama Weibull cobraba el rezago de obra dos veces.
            benefits_timed = benefits_raw * tf - cod_total + degraded_delta_r_npv
            net_value_t = benefits_timed + cost_npv_t
            # El VEI divide por el CAPEX a valor presente — el mismo denominador
            # que usa evaluate(). Numerador y denominador en la misma moneda.
            vei_t = net_value_t / capex_pv_t if capex_pv_t > 0 else 0.0

            yearly_results.append({
                "year":                  year,
                "delay_years":           t,
                "capex_ajustado_COP":    capex_t,      # nominal, pesos del año t
                "capex_pv_COP":          capex_pv_t,   # el que entra a la ecuación
                "delta_r_annual_COP":    degraded_delta_r_annual,
                "delta_r_npv_COP":       degraded_delta_r_npv,
                "benefits_raw_COP":      benefits_raw,
                "timing_factor":         tf,
                "cod_total_COP":         cod_total,
                "cost_npv_COP":          cost_npv_t,
                "net_value_COP":         net_value_t,
                "vei":                   vei_t,
            })

        # ── Identificar el año óptimo (máximo VNT) ──────────────────────────
        optimal = max(yearly_results, key=lambda r: r["net_value_COP"])
        t0_result = yearly_results[0]

        # ── Señales de alerta para el MILP ──────────────────────────────────
        # Si el año óptimo es t=0, el proyecto no puede esperar
        urgency = "AHORA"   if optimal["delay_years"] == 0 else                   "PRONTO"  if optimal["delay_years"] <= 1 else                   "FLEXIBLE"

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


    def evaluate_with_timing(self, project: "ProjectInput") -> dict:
        """
        Evaluación completa con Análisis de Tiempo Óptimo de Intervención.
        Combina evaluate() + optimal_timing_analysis() en una sola llamada.
        Úsalo cuando el analista quiere saber no solo cuánto vale el proyecto
        sino CUÁNDO ejecutarlo para maximizar el valor del ciclo de vida.
        """
        result = self.evaluate(project)
        timing_analysis = self.optimal_timing_analysis(project)
        result["optimal_timing"] = timing_analysis
        # Exponer el año óptimo en el nivel superior para el MILP
        result["recommended_start_year"] = timing_analysis["optimal_year"]
        result["urgency"] = timing_analysis["urgency"]
        return result

    def _get_model_level(self, capex: float,
                          business_unit: str = "Todos") -> str:
        """
        Nivel de rigor analítico según CAPEX y Unidad de Negocio.
        Lee umbrales por BU (TyD/AyA/Gas/Generacion/Todos) del config.
        Fallback a umbrales globales si la BU no tiene definición propia.
        Ref: ISO 55001:2024 §4.5.4 — IAM SSG Fig. 7.
        """
        thresholds = self.config.get("proportionality_thresholds_COP", {})
        bu_thr = thresholds.get(business_unit) or thresholds.get("Todos") or {}
        full  = bu_thr.get("full_model_COP")       or thresholds.get("full_model")
        simpl = bu_thr.get("simplified_model_COP") or thresholds.get("simplified_model")
        if full  and capex >= full:  return "FULL"
        if simpl and capex >= simpl: return "SIMPLIFIED"
        return "RULE_BASED"

# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN D: CONCEPT RATING — ESCALA 7 NIVELES EPM
# ══════════════════════════════════════════════════════════════════════════════
# Mecanismo: Copperleaf CRD (invariable)
# Parámetros: EPM — definidos por ASPD en proceso de calibración institucional
# Escala: 0-6, nivel 3 = Caso Base (estado actual EPM)
# Delta negativo (0-2) → penalización. Delta positivo (4-6) → suma valor.

CR_BASELINE_LEVEL = 3

CR_SCALE_MAX_BY_BU = {
    "Reputacion": {
        "TyD": 280_000_000_000, "AyA": 320_000_000_000,
        "Generacion": 380_000_000_000, "Gas": 180_000_000_000,
        "Todos": 420_000_000_000
    },
    "Rentabilidad": {
        "TyD": 350_000_000_000, "AyA": 400_000_000_000,
        "Generacion": 600_000_000_000, "Gas": 200_000_000_000,
        "Todos": 1_750_000_000_000
    }
}

CR_LEVELS = {
    "VD-1-F": {
        "label": "Reputación EPM (NPS + CX Forces + imagen institucional)",
        "unidad": "Estado reputacional resultante del proyecto",
        "caso_base": 3,
        "nota": "Ancla nivel 0: crisis tipo Hidroituango 2018 o superior.",
        "niveles": [
            "0 — Crisis sistémica. Pérdida grado inversión internacional. Intervención regulatoria. "
                "[Referencia: Hidroituango 2018 o superior]",
            "1 — Crisis local grave. Cobertura nacional negativa sostenida. Investigaciones activas.",
            "2 — Deterioro significativo. NPS cae >10 pts. PQRS aumenta. Escrutinio regulatorio.",
            "3 — Estado estándar EPM [CASO BASE]. Sin cambio. NPS histórico. Sin incidentes.",
            "4 — Mejora moderada. NPS sube 5-15 pts. Reconocimiento positivo local.",
            "5 — Mejora significativa. NPS sube >15 pts. Reconocimiento sectorial nacional.",
            "6 — Liderazgo reputacional. Referente nacional/internacional. NPS máximo histórico."
        ]
    },
    "VD-5-C": {
        "label": "Rentabilidad del portafolio de activos EPM",
        "unidad": "Estado de rentabilidad resultante del proyecto",
        "caso_base": 3,
        "nota": "Ancla nivel 6: delta ≥5pp sobre benchmark regulado CREG.",
        "niveles": [
            "0 — Destrucción de valor. Severamente por debajo del costo de capital.",
            "1 — Rentabilidad crítica. Delta >3pp bajo benchmark CREG.",
            "2 — Rentabilidad deficiente. Delta 1-3pp bajo benchmark.",
            "3 — Rentabilidad estándar [CASO BASE]. En línea con benchmark CREG.",
            "4 — Rentabilidad buena. Delta 1-2pp sobre benchmark.",
            "5 — Rentabilidad significativa. Delta 2-4pp sobre benchmark.",
            "6 — Rentabilidad excepcional. Delta ≥5pp. Liderazgo utilities Colombia."
        ]
    }
}


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN E: WIZARD DE CAPTURA GUIADA
# ══════════════════════════════════════════════════════════════════════════════

class Colors:
    GREEN  = "\033[92m"; CYAN   = "\033[96m"; YELLOW = "\033[93m"
    RED    = "\033[91m"; BOLD   = "\033[1m";  DIM    = "\033[2m"; RESET  = "\033[0m"

def c(text, color): return f"{color}{text}{Colors.RESET}"
def cop(v):
    """Nomenclatura colombiana: M=millón, MM=mil millones, B=billón (1e12)."""
    s = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e12: return f"{s}COP {a/1e12:,.2f}B"
    if a >= 1e9:  return f"{s}COP {a/1e9:,.1f}MM"
    if a >= 1e6:  return f"{s}COP {a/1e6:,.1f}M"
    if a >= 1e3:  return f"{s}COP {a/1e3:,.0f}K"
    return f"{s}COP {a:,.0f}"

def section_header(title: str):
    print(); print(c("  ─" * 35, Colors.CYAN))
    print(c(f"  {title}", Colors.BOLD + Colors.CYAN))
    print(c("  ─" * 35, Colors.CYAN)); print()

def ask(prompt: str, required: bool = True, cast=str, default=None):
    while True:
        raw = input(c(f"  ▸ {prompt}: ", Colors.CYAN)).strip()
        if not raw:
            if default is not None: return default
            if not required: return None
            print(c("    Campo requerido.", Colors.RED)); continue
        try:
            return cast(raw)
        except (ValueError, TypeError):
            print(c(f"    Formato inválido. Esperado: {cast.__name__}", Colors.RED))

def ask_choice(prompt: str, options: list, default: int = 1) -> int:
    for i, o in enumerate(options, 1):
        marker = c("►", Colors.YELLOW) if i == default else " "
        print(c(f"    [{i}]", Colors.YELLOW) + f" {marker} {o}")
    while True:
        raw = input(c(f"  ▸ {prompt} [{default}]: ", Colors.CYAN)).strip()
        if not raw: return default
        try:
            v = int(raw)
            if 1 <= v <= len(options): return v
        except ValueError: pass
        print(c(f"    Ingresa un número entre 1 y {len(options)}.", Colors.RED))

def ask_concept_rating(sc_id: str, label: str, bu: str = "Todos") -> float:
    """Concept Rating EPM — escala 0-6, nivel 3 = Caso Base."""
    cr_def = CR_LEVELS.get(sc_id, {
        "label": label, "unidad": "Nivel de impacto",
        "caso_base": 3, "nota": "",
        "niveles": [
            "0 — Impacto muy negativo.", "1 — Impacto negativo.",
            "2 — Deterioro leve.",        "3 — Sin cambio [CASO BASE].",
            "4 — Mejora leve.",           "5 — Mejora significativa.",
            "6 — Impacto máximo positivo."
        ]
    })
    print(c(f"\n    ╔══ CONCEPT RATING — {cr_def['label']}", Colors.GREEN + Colors.BOLD))
    print(c(f"    ║   {cr_def['unidad']}", Colors.DIM))
    if cr_def.get("nota"):
        print(c(f"    ║   {cr_def['nota']}", Colors.YELLOW))
    print(c(f"    ╠══ Selecciona el estado RESULTANTE del proyecto:", Colors.CYAN))
    for nivel in cr_def["niveles"]:
        num = nivel[0]
        desc = nivel[4:]
        marker = c(" ★ CASO BASE", Colors.GREEN) if int(num) == 3 else ""
        print(c(f"    ║  [{num}]", Colors.YELLOW) + c(f" {desc}", Colors.DIM) + marker)
    print(c(f"    ╚══", Colors.GREEN))
    while True:
        raw = input(c("  ▸ Nivel [0-6]: ", Colors.CYAN)).strip()
        try:
            v = int(raw)
            if 0 <= v <= 6:
                delta = v - 3
                ds = f"+{delta}" if delta > 0 else str(delta)
                color = Colors.GREEN if delta > 0 else Colors.RED if delta < 0 else Colors.DIM
                print(c(f"    ✓ Nivel {v} — Delta vs Caso Base: {ds}", color))
                return float(v)
        except ValueError: pass
        print(c("    Ingresa un número entre 0 y 6.", Colors.RED))


class ProjectWizard:
    """Wizard de captura guiada de proyectos EPM."""

    def __init__(self, config: dict):
        self.config = config
        self._build_sc_index()

    def _build_sc_index(self):
        """Índice de sub-criterios del config para acceso rápido."""
        self._sc_index = {}
        for vd in self.config.get("value_drivers", []):
            for sc in vd.get("sub_criteria", []):
                self._sc_index[sc["id"]] = sc

    def run(self) -> Optional[ProjectInput]:
        section_header("EVALUACIÓN DE PROYECTO — WIZARD")

        # 1. Datos básicos
        print(c("  DATOS DEL PROYECTO", Colors.BOLD))
        pid   = ask("ID del proyecto (ej. TYD-SUB-NOR-031)")
        name  = ask("Nombre descriptivo")

        print()
        bu_options = ["TyD", "AyA", "Generacion", "Gas", "Todos"]
        print(c("  Unidad de Negocio:", Colors.BOLD))
        bu_idx = ask_choice("Selecciona", bu_options)
        bu = bu_options[bu_idx - 1]

        # 2. Tipo de inversión
        print()
        print(c("  Tipo de Inversión:", Colors.BOLD))
        inv_types = list(INVESTMENT_TYPES.keys())
        for i, (k, v) in enumerate(INVESTMENT_TYPES.items(), 1):
            print(c(f"    [{i}]", Colors.YELLOW) + f" {k} — {c(v['description'], Colors.DIM)}")
        inv_idx = ask_choice("Selecciona el tipo", list(range(1, len(inv_types)+1)), 1)
        inv_type = inv_types[inv_idx - 1]
        inv_def = INVESTMENT_TYPES[inv_type]

        # 3. Costos
        print()
        print(c("  COSTOS", Colors.BOLD))
        capex = ask("CAPEX total del proyecto (COP)", cast=float)
        opex = ask("OPEX incremental anual (COP/año, 0 si no aplica)",
                   cast=float, default=0.0, required=False) or 0.0

        # 4. Timing y Temporalidad
        print()
        print(c("  TIMING Y TEMPORALIDAD", Colors.BOLD))
        current_year = datetime.now().year
        opt_year  = ask(f"Año óptimo de ejecución [{current_year}]",
                        cast=int, default=current_year, required=False) or current_year
        exec_year = ask(f"Año de ejecución real [{opt_year}]",
                        cast=int, default=opt_year, required=False) or opt_year
        if exec_year < opt_year:
            print(c("    Advertencia: año real anterior al óptimo — se ajusta al óptimo.", Colors.YELLOW))
            exec_year = opt_year

        print()
        print(c("  DURACIÓN DE CONSTRUCCIÓN", Colors.BOLD))
        print(c("    Años que el proyecto consume CAPEX sin entregar beneficios.", Colors.DIM))
        print(c("    Ref: subestación nueva 2-3a · PRAS 1-2a · software 1a · obra lineal 3-5a", Colors.DIM))
        construction_years = ask("  Años de construcción [1]",
                                  cast=int, default=1, required=False) or 1

        benefit_offsets = {}
        if construction_years > 0:
            print()
            print(c("  INICIO DE BENEFICIOS POR RETO (años adicionales post-construcción)", Colors.BOLD))
            print(c("    0 = el reto genera valor desde el primer día de operación (más común).", Colors.DIM))
            print(c("    1 = ese reto tarda 1 año más en madurar (ej. rampa de nuevas conexiones).", Colors.DIM))
            print(c("    Presiona Enter para aceptar 0.", Colors.DIM))
            for vd_id, vd_lbl in [
                ("VD-1", "Calidad / Continuidad"),
                ("VD-2", "Eficiencia / OPEX"),
                ("VD-3", "Cobertura / Conexiones"),
                ("VD-4", "Carbono"),
                ("VD-5", "Generación de Valor"),
                ("VD-6", "Reputación"),
            ]:
                offset = ask(f"  Offset {vd_id} — {vd_lbl} [0]",
                              cast=int, default=0, required=False) or 0
                if offset > 0:
                    benefit_offsets[vd_id] = offset

        # 5. Sub-criterios
        print()
        print(c(f"  BENEFICIOS — Tipo: {inv_type}", Colors.BOLD))
        mandatory = inv_def["mandatory_sc"]
        suggested = inv_def["suggested_sc"]

        print(c("  Sub-criterios obligatorios para este tipo:", Colors.DIM))
        for sc_id in mandatory:
            sc_def = self._sc_index.get(sc_id)
            if sc_def:
                print(c(f"    • {sc_id}: {sc_def['label']}", Colors.GREEN))

        # ── Filtrar SCs por BU antes de mostrar al analista ──────────────────
        def sc_applies_to_bu(sc_id: str) -> bool:
            """True si el SC aplica a la BU del proyecto."""
            sc_d = self._sc_index.get(sc_id, {})
            bu_f = sc_d.get("bu_filter", [])
            return len(bu_f) == 0 or bu in bu_f

        mandatory_filtered = [s for s in mandatory if sc_applies_to_bu(s)]
        excl = [s for s in mandatory if s not in mandatory_filtered]
        if excl:
            print(c(f"  Nota: {excl} no aplican a {bu} — excluidos automáticamente.", Colors.YELLOW))

        all_sc_ids = mandatory_filtered.copy()
        suggested_filtered = [s for s in suggested
                               if s not in all_sc_ids and sc_applies_to_bu(s)]

        print()
        if suggested_filtered:
            print(c("  ¿Deseas agregar sub-criterios adicionales?", Colors.BOLD))
            print(c(f"  (Solo se muestran los que aplican a {bu})", Colors.DIM))
            for sc_id in suggested_filtered:
                sc_def = self._sc_index.get(sc_id)
                if sc_def:
                    resp = input(c(f"    Incluir {sc_id} — {sc_def['label']}? [s/N]: ",
                                   Colors.DIM)).strip().lower()
                    if resp == "s":
                        all_sc_ids.append(sc_id)
        else:
            print(c("  No hay sub-criterios adicionales para esta BU y tipo de inversión.", Colors.DIM))

        # Capturar datos por sub-criterio
        drivers_data = {}
        for sc_id in all_sc_ids:
            sc_def = self._sc_index.get(sc_id)
            if not sc_def:
                continue
            # Agrupar por VD padre
            vd_id = sc_id[:4]
            if vd_id not in drivers_data:
                drivers_data[vd_id] = {"sc_inputs": []}

            sc_input = self._capture_sc(sc_def, bu)
            if sc_input:
                drivers_data[vd_id]["sc_inputs"].append(sc_input)

        # Construir DriverInputs
        driver_inputs = []
        for vd in self.config.get("value_drivers", []):
            vd_id = vd["id"]
            if vd_id in drivers_data and drivers_data[vd_id]["sc_inputs"]:
                driver_inputs.append(DriverInput(
                    id=vd_id,
                    reto=vd["reto"],
                    sub_criteria=drivers_data[vd_id]["sc_inputs"]
                ))

        # 6. Riesgo
        risk_input = None
        print()
        print(c("  PERFIL DE RIESGO", Colors.BOLD))
        include_risk = input(c("  ¿Incluir análisis de riesgo? [S/n]: ", Colors.CYAN)).strip().lower()
        if include_risk != "n":
            risk_input = self._capture_risk()

        # 7. Dependencias
        deps = []
        print()
        dep_resp = input(c("  ¿Este proyecto depende de otro? [s/N]: ", Colors.DIM)).strip().lower()
        if dep_resp == "s":
            dep_id = ask("ID del proyecto requerido")
            deps.append(dep_id)

        # 8. ¿Es no discrecional?
        mandatory_resp = input(c("  ¿Es no discrecional (must-do regulatorio)? [s/N]: ", Colors.DIM)).strip().lower()
        is_mandatory = mandatory_resp == "s"

        # ── Asset ID para Must-Mitigate ────────────────────────────
        print()
        print(c("  ACTIVO ASOCIADO (Must-Mitigate)", Colors.BOLD))
        print(c("  Si este proyecto interviene un activo con riesgo inaceptable,", Colors.DIM))
        print(c("  el optimizador lo forzará si ningún otro proyecto lo mitiga.", Colors.DIM))
        asset_id_resp = ask("  ID del activo (ej. TyD-TR-001, AyA-PTAP-Norte, Enter=ninguno)",
                            required=False, default="")

        # ── Exclusión mutua entre proyectos ─────────────────────────
        print()
        print(c("  EXCLUSIÓN MUTUA ENTRE PROYECTOS", Colors.BOLD))
        print(c("  Declara si este proyecto compite con otros (misma necesidad, distintos enfoques).", Colors.DIM))
        print(c("  El optimizador no podrá seleccionar más de uno del mismo grupo.", Colors.DIM))

        excl_grp = None
        mutex_list: list = []

        excl_resp = input(c("  ¿Este proyecto pertenece a un grupo de alternativas excluyentes? [s/N]: ",
                            Colors.DIM)).strip().lower()
        if excl_resp == "s":
            excl_grp = ask("  ID del grupo (ej. 'sub-norte-v1', 'linea-132kv')")
            print(c(f"  Grupo '{excl_grp}' asignado.", Colors.GREEN))
            print(c("  (Los demás proyectos del mismo grupo deben declarar el mismo ID)", Colors.DIM))

        pair_resp = input(c("  ¿Hay proyectos específicos MUTUAmente excluyentes con éste? [s/N]: ",
                            Colors.DIM)).strip().lower()
        if pair_resp == "s":
            print(c("  Ingresa los IDs separados por coma (ej. P001, P002):", Colors.DIM))
            ids_raw = input(c("  IDs excluyentes: ", Colors.CYAN)).strip()
            mutex_list = [s.strip() for s in ids_raw.split(",") if s.strip()]
            if mutex_list:
                print(c(f"  Registrados {len(mutex_list)} excluyentes: {mutex_list}", Colors.GREEN))

        proj_result = ProjectInput(
            id=pid, name=name,
            business_unit=bu,
            investment_type=inv_type,
            costs=CostInput(capex_COP=capex, opex_incremental_COP_year=opex),
            execution_year=exec_year,
            optimal_year=opt_year,
            drivers=driver_inputs,
            risk=risk_input,
            dependencies=deps,
            is_mandatory=is_mandatory,
            construction_years=construction_years,
            benefit_start_offsets=benefit_offsets
        )
        proj_result.asset_id           = asset_id_resp or ""
        proj_result.exclusion_group_id = excl_grp
        proj_result.mutual_exclusion_ids = mutex_list
        return proj_result

    # ── GWP lookup por gas (Carbono Neutralidad wizard) ──────────────────────
    _GWP = {
        "CO2": (1,       "tCO₂/año",   "combustión directa, procesos industriales"),
        "CH4": (25,      "tCH₄/año",   "fugas redes gas, digestores, rellenos"),
        "N2O": (298,     "tN₂O/año",   "PTAR, procesos biológicos AyA"),
        "SF6": (23500,   "kg SF₆/año", "interruptores y equipos AT con gas SF₆"),
        "HFC": (None,    "kg/año",     "refrigerantes en climatización subestaciones/plantas"),
    }
    # Gases relevantes por BU (filtrado dinámico de la pregunta 1)
    _GWP_BU = {
        "TyD":       ["CO2", "SF6", "HFC"],
        "Generacion":["CO2", "CH4", "HFC"],
        "AyA":       ["CO2", "CH4", "N2O"],
        "Gas":       ["CO2", "CH4"],
        "Todos":     ["CO2", "CH4", "N2O", "SF6", "HFC"],
    }

    def _capture_vd4_carbon(self, bu: str, weight: float = 1.0) -> SubCriterionInput:
        """Wizard multi-paso para VD-4-A Carbono Neutralidad.

        Flujo:
          1. Multi-select de gases relevantes a la BU
          2. Por cada gas: pregunta en unidad física natural
          3. Conversión automática a tCO₂eq con GWP calibrado
          4. Resumen desglosado + confirmación
        Retorna SubCriterionInput con delta_annual = total tCO₂eq/año.
        """
        cf = self.config.get("carbon_factors", {})
        gwp_cfg = cf.get("gwp", {})
        precio  = cf.get("precio_carbono_cop_tco2eq", 88_000)

        print()
        print(c("  CARBONO NEUTRALIDAD — Scope 1 y Scope 2 (Definición EPM)", Colors.BOLD))
        print(c("  Gases: CO₂ · CH₄ · N₂O · HFCs · SF₆ | Factor SIN 0.126 tCO₂eq/MWh", Colors.DIM))

        gases_bu = self._GWP_BU.get(bu, self._GWP_BU["Todos"])

        # ── Pregunta 1: selección de gases ──────────────────────────────────
        print()
        print(c("  ¿Qué tipo(s) de emisiones reduce este proyecto? (0 para omitir)", Colors.CYAN))
        gas_opts = []
        for i, gas in enumerate(gases_bu, 1):
            gwp_val, unit, desc = self._GWP[gas]
            gwp_str = f"GWP {gwp_val:,}" if gwp_val else "GWP variable"
            print(f"    [{i}] {gas:<6}  {gwp_str:<14}  {unit:<14}  — {desc}")
        print(f"    [0] Ninguno / ingresar tCO₂eq directamente")

        seleccion_raw = ask("  Seleccione (ej: 1 3 4)", cast=str, default="0")
        indices = []
        for tok in seleccion_raw.split():
            try:
                v = int(tok)
                if 1 <= v <= len(gases_bu):
                    indices.append(v - 1)
            except ValueError:
                pass

        # ── Pregunta 2-N: cantidad por gas ──────────────────────────────────
        desglose: list[tuple[str, float, float, str]] = []  # (gas, cantidad, tco2eq, unidad)

        if not indices:
            # Fallback: entrada directa en tCO₂eq
            print(c("  Ingresa el total directamente en tCO₂eq/año.", Colors.DIM))
            total = ask("  Total tCO₂eq/año", cast=float, default=0.0) or 0.0
            desglose.append(("tCO₂eq directo", total, total, "tCO₂eq/año"))
        else:
            for idx in indices:
                gas = gases_bu[idx]
                gwp_val, unit, desc = self._GWP[gas]
                print()
                print(c(f"  → {gas}: {desc}", Colors.YELLOW))

                if gas == "HFC":
                    # Selección de refrigerante
                    hfc_opts = [
                        ("R-134a", gwp_cfg.get("HFC_R134a", 1430)),
                        ("R-410A", gwp_cfg.get("HFC_R410A", 2088)),
                        ("R-22",   gwp_cfg.get("HFC_R22",   1810)),
                        ("Otro",   None),
                    ]
                    print(c("  ¿Qué refrigerante?", Colors.CYAN))
                    for i, (nm, gv) in enumerate(hfc_opts, 1):
                        print(f"    [{i}] {nm}  GWP {gv if gv else '?'}")
                    hfc_idx = (ask("  Opción [1-4]", cast=int, default=1) or 1) - 1
                    hfc_nm, hfc_gwp = hfc_opts[min(hfc_idx, 3)]
                    if hfc_gwp is None:
                        hfc_gwp = ask("  GWP del refrigerante", cast=float)
                    cantidad = ask(f"  kg {hfc_nm}/año", cast=float, default=0.0) or 0.0
                    tco2eq   = cantidad * hfc_gwp / 1000  # kg → t, ×GWP
                    print(c(f"    {cantidad:.2f} kg × GWP {hfc_gwp:,} = {tco2eq:,.1f} tCO₂eq/año", Colors.GREEN))
                    desglose.append((f"HFC {hfc_nm}", cantidad, tco2eq, "kg/año"))

                elif gwp_val == 23500:  # SF₆ — unidad kg, no toneladas
                    print(c(f"  Tasa fuga típica: 0.1%–1% del volumen del equipo/año", Colors.DIM))
                    print(c(f"  Ej: interruptor 145kV ≈ 5 kg SF₆ → fuga 0.5%/año = 0.025 kg/año", Colors.DIM))
                    cantidad = ask(f"  kg SF₆/año", cast=float, default=0.0) or 0.0
                    tco2eq   = cantidad * gwp_val / 1000  # kg → t, ×GWP
                    print(c(f"    {cantidad:.3f} kg × {gwp_val:,} = {tco2eq:,.1f} tCO₂eq/año", Colors.GREEN))
                    desglose.append((gas, cantidad, tco2eq, unit))

                else:
                    gwp_str = f"GWP {gwp_val}" if gwp_val else ""
                    cantidad = ask(f"  {unit}", cast=float, default=0.0) or 0.0
                    tco2eq   = cantidad * (gwp_val or 1)
                    print(c(f"    {cantidad:.2f} × {gwp_val} = {tco2eq:,.1f} tCO₂eq/año", Colors.GREEN))
                    desglose.append((gas, cantidad, tco2eq, unit))

        # ── Resumen y confirmación ───────────────────────────────────────────
        total_tco2eq = sum(d[2] for d in desglose)
        valor_cop    = total_tco2eq * precio
        print()
        print(c("  RESUMEN CARBONO NEUTRALIDAD (Scope 1 + Scope 2 GEI)", Colors.BOLD))
        print(c("  " + "─" * 60, Colors.DIM))
        for gas, qty, tco2, unit in desglose:
            print(f"    {gas:<18}  {qty:>10.3f} {unit:<14}  {tco2:>10,.1f} tCO₂eq/año")
        print(c("  " + "─" * 60, Colors.DIM))
        print(c(f"  TOTAL:  {total_tco2eq:>10,.1f} tCO₂eq/año", Colors.BOLD))
        print(c(f"  Valor:  {cop(valor_cop)}/año  (precio carbono {cop(precio)}/tCO₂eq)", Colors.GREEN))
        print()

        confirma = ask("  ¿Confirma? [S/n]", cast=str, default="S")
        if confirma.strip().upper() == "N":
            return self._capture_vd4_carbon(bu, weight)  # reinicia

        return SubCriterionInput(
            id="VD-4-A", label="Reducción emisiones GEI (tCO₂eq) — Scope 1+2",
            type="monetizable",
            delta_annual=total_tco2eq,
            unit="tCO2eq/año",
            weight=weight,
            price_per_unit=precio,
        )

    def _capture_sc(self, sc_def: dict, bu: str) -> Optional[SubCriterionInput]:
        """Captura datos de un sub-criterio según su tipo."""
        sc_id    = sc_def["id"]
        label    = sc_def["label"]
        sc_type  = sc_def.get("type", "monetizable")
        weight   = sc_def.get("weight", 1.0)

        print()
        print(c(f"  [{sc_id}] {label}", Colors.BOLD))

        # ── VD-4-A: wizard especializado por gas y GWP ──────────────────────
        if sc_id == "VD-4-A":
            return self._capture_vd4_carbon(bu, weight)

        if sc_type == "monetizable":
            unit  = sc_def.get("unit", "unidad")
            price = sc_def.get("price_per_unit_COP")
            print(c(f"    Ingresa la cantidad física DELTA (con proyecto – sin proyecto).", Colors.DIM))
            print(c(f"    Unidad: {unit}", Colors.YELLOW))
            if price:
                print(c(f"    Precio calibrado: {cop(price)}/{unit.split('/')[0]}", Colors.DIM))
            delta = ask(f"  Delta anual ({unit})", cast=float, default=0.0, required=False) or 0.0
            if price is None:
                price = ask(f"  Precio por unidad (COP/{unit})", cast=float)
            return SubCriterionInput(
                id=sc_id, label=label, type="monetizable",
                delta_annual=delta, unit=unit,
                weight=weight, price_per_unit=price
            )
        else:
            # Concept Rating
            scale_max = sc_def.get("scale_max_COP")
            scale_by_bu = sc_def.get("scale_max_by_bu", {})
            if bu in scale_by_bu:
                scale_max = scale_by_bu[bu]
            nivel = ask_concept_rating(sc_id, label, bu)
            return SubCriterionInput(
                id=sc_id, label=label, type="concept_rating",
                delta_annual=nivel, unit="nivel_CR_0_6",
                weight=weight,
                value_function=sc_def.get("value_function", "s_curve"),
                scale_max_COP=scale_max,
                vf_inflection=sc_def.get("vf_inflection", 5.0),
                vf_steepness=sc_def.get("vf_steepness", 1.5)
            )

    def _capture_risk(self) -> RiskInput:
        rm = self.config.get("risk_matrix", {})
        cl = rm.get("consequence_levels", {})
        pl = rm.get("probability_levels", {})

        print(c("  Consecuencias disponibles:", Colors.DIM))
        for k, v in cl.items():
            print(f"    [{k}] {v['label']} — {cop(v['value_COP'])}")
        print(c("  Probabilidades disponibles:", Colors.DIM))
        for k, v in pl.items():
            print(f"    [{k}] {v['label']} — {v['annual_prob']*100:.1f}%/año")

        print()
        print(c("  CASO BASE (sin proyecto):", Colors.BOLD))
        bc = ask("  Nivel consecuencia Caso Base [1-5]", cast=int, default=3) or 3
        bp = ask("  Nivel probabilidad Caso Base [1-5]", cast=int, default=3) or 3
        print(c("  RIESGO RESIDUAL (con proyecto):", Colors.BOLD))
        rc = ask("  Nivel consecuencia residual [1-5]", cast=int, default=1) or 1
        rp = ask("  Nivel probabilidad residual [1-5]", cast=int, default=1) or 1
        return RiskInput(bc, bp, rc, rp)


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN F: INTERVENTION ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def _wrap_note(text: str, width: int = 96) -> List[str]:
    """Parte un texto en líneas de ancho fijo sin cortar palabras."""
    if not text:
        return []
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


@dataclass
class InterventionOption:
    """Una opción de intervención para una necesidad.

    deferral_years permite que dos opciones difieran solo en CUÁNDO se hace.
    Sin este campo la comparación no podía expresar "lo mismo, pero dentro de
    tres años", que es justo la pregunta que el análisis de oportunidad debe
    responder: evaluate() lo soporta desde siempre, pero esta capa no lo
    exponía y toda opción se evaluaba como si arrancara hoy.
    """
    option_id: str
    label: str
    project: ProjectInput
    deferral_years: int = 0

@dataclass
class NeedDecision:
    """Resultado de evaluar todas las opciones de una necesidad.

    La recomendación viene acompañada de su ESTADO: si la banda de VNT de la
    ganadora está separada de la de la siguiente, la elección está resuelta por
    los datos; si se traslapan, el estimativo no las distingue y el desempate
    corresponde al optimizador bajo restricción presupuestal, no a esta etapa.

    Una decisión NO RESUELTA no es un defecto — es información honesta sobre la
    calidad del dato, y las alternativas empatadas deben seguir vivas hacia el
    optimizador dentro de un grupo de exclusión mutua.
    """
    need_id: str
    need_label: str
    options_evaluated: list
    recommended_option_id: str
    recommended_net_value: float
    recommended_vei: float
    # ── Veredicto de incertidumbre de estimación ──────────────────────────────
    verdict: str = "RESUELTA"          # RESUELTA | NO_RESUELTA | UNICA_OPCION
    overlap_fraction: float = 0.0      # Traslape con la 2ª mejor [0-1]
    runner_up_option_id: str = ""
    tied_option_ids: list = field(default_factory=list)
    """Alternativas cuya banda se traslapa con la ganadora. Son las que deben
    entrar vivas al optimizador si el veredicto es NO_RESUELTA."""
    verdict_note: str = ""


class InterventionEngine:
    """
    Evalúa múltiples opciones de intervención para una misma necesidad.
    Solo UNA opción por necesidad puede entrar al portafolio.
    """

    def __init__(self, config: dict):
        self.engine = ValueEngine(config)

    def evaluate_need(self, need_id: str, need_label: str,
                      options: list,
                      include_timing: bool = True) -> NeedDecision:
        """
        Evalúa opciones de intervención. Con include_timing=True (default),
        cada opción incluye el Análisis de Tiempo Óptimo de Copperleaf.
        """
        results = []
        for opt in options:
            _dif = int(getattr(opt, "deferral_years", 0) or 0)
            if include_timing:
                r = self.engine.evaluate_with_timing(opt.project)
            else:
                r = self.engine.evaluate(opt.project, deferral_years=_dif)
            band = self.engine.estimate_band(opt.project, base_result=r,
                                             deferral_years=_dif)
            results.append({
                "option_id": opt.option_id,
                "label": opt.label,
                "net_value_COP": r["net_value_COP"],
                "vei": r["value_efficiency_index"],
                "capex_COP": opt.project.costs.capex_COP,
                "optimal_year": r.get("recommended_start_year", opt.project.optimal_year),
                "urgency": r.get("urgency", "FLEXIBLE"),
                "estimate_band": band,
                "estimate_class": band["estimate_class"],
                "net_value_low_COP":  band["net_value_low_COP"],
                "net_value_high_COP": band["net_value_high_COP"],
                "result": r
            })

        results.sort(key=lambda x: x["net_value_COP"], reverse=True)
        best = results[0]

        # ── Veredicto por traslape de bandas de estimación ────────────────────
        # Entre alternativas de una misma necesidad, la incertidumbre común
        # (mundo, mercado, precios) se cancela: mueve a todas en el mismo
        # sentido. Lo que puede invertir el orden es que una esté bien estimada
        # y otra no. Por eso el veredicto se construye SOLO sobre la banda de
        # estimación, no sobre supuestos del marco.
        _cfg_unc  = self.engine.config.get("cost_estimate_uncertainty", {})
        threshold = float(_cfg_unc.get("overlap_threshold", 0.0))

        verdict, overlap, runner_up = "UNICA_OPCION", 0.0, ""
        tied: list = []
        note = "Solo hay una alternativa evaluada — no hay comparación que resolver."

        if len(results) > 1:
            runner_up = results[1]["option_id"]
            overlap = ValueEngine.overlap_fraction(
                best["estimate_band"], results[1]["estimate_band"])

            # Todas las que se traslapan con la ganadora quedan vivas
            for o in results[1:]:
                if ValueEngine.overlap_fraction(
                        best["estimate_band"], o["estimate_band"]) > threshold:
                    tied.append(o["option_id"])

            if overlap > threshold:
                verdict = "NO_RESUELTA"
                note = (
                    f"Las bandas de {best['label']} y {results[1]['label']} se traslapan "
                    f"{overlap*100:.0f}%. Con la calidad actual de los estimativos el modelo "
                    f"no distingue entre ellas: el orden puede invertirse. "
                    f"Mejorar la base del estimativo, o dejar que el desempate lo haga el "
                    f"optimizador bajo restricción presupuestal — entre alternativas que "
                    f"empatan en valor, la más barata libera cupo para otras necesidades."
                )
            else:
                verdict = "RESUELTA"
                note = (
                    f"La banda de {best['label']} no toca la de {results[1]['label']}. "
                    f"La elección se sostiene en todo el rango de incertidumbre de "
                    f"estimación de ambas."
                )

        return NeedDecision(
            need_id=need_id,
            need_label=need_label,
            options_evaluated=results,
            recommended_option_id=best["option_id"],
            recommended_net_value=best["net_value_COP"],
            recommended_vei=best["vei"],
            verdict=verdict,
            overlap_fraction=overlap,
            runner_up_option_id=runner_up,
            tied_option_ids=tied,
            verdict_note=note,
        )

    def print_need_report(self, decision: NeedDecision):
        section_header(f"OPCIONES DE INTERVENCIÓN — {decision.need_label}")
        print(f"  {'Opción':<26} {'VNT (rango estimación)':>38} {'Base':>7} {'CAPEX':>16} {'Año Ópt.':>9}")
        print(c("  " + "─" * 104, Colors.DIM))
        for opt in decision.options_evaluated:
            marker = c(" ◄", Colors.GREEN) if opt["option_id"] == decision.recommended_option_id else "  "
            band = opt.get("estimate_band", {})
            rango = (f"{cop(opt.get('net_value_low_COP', 0)):>17}"
                     f" – {cop(opt.get('net_value_high_COP', 0)):<17}")
            cls = band.get("estimate_class", "?").replace("Class", "C")
            cls_txt = c(cls, Colors.DIM) if band.get("is_default") else cls
            print(f"  {opt['label']:<26} {rango:>38} {cls_txt:>7} "
                  f"{cop(opt['capex_COP']):>16} {str(opt.get('optimal_year','?')):>9}{marker}")
        print()

        # ── Veredicto ─────────────────────────────────────────────────────────
        _v = decision.verdict
        _color = (Colors.GREEN if _v == "RESUELTA"
                  else Colors.YELLOW if _v == "NO_RESUELTA" else Colors.DIM)
        _titulo = {
            "RESUELTA":     "DECISIÓN RESUELTA POR LOS DATOS",
            "NO_RESUELTA":  "DECISIÓN NO RESUELTA POR LOS DATOS",
            "UNICA_OPCION": "ALTERNATIVA ÚNICA",
        }.get(_v, _v)

        print(c(f"  {_titulo}", _color + Colors.BOLD))
        print(c(f"  Recomendada: {decision.recommended_option_id} | "
                f"VNT {cop(decision.recommended_net_value)} | "
                f"VEI {decision.recommended_vei:.2f}x", _color))
        if decision.verdict == "NO_RESUELTA":
            print(c(f"  Traslape con 2ª mejor: {decision.overlap_fraction*100:.0f}%", Colors.YELLOW))
            print(c(f"  Alternativas vivas al optimizador: "
                    f"{', '.join([decision.recommended_option_id] + decision.tied_option_ids)}",
                    Colors.YELLOW))
        for _ln in _wrap_note(decision.verdict_note, 96):
            print(c(f"  {_ln}", Colors.DIM))
        print()


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN G: PORTFOLIO OPTIMIZER — MILP EXACTO (scipy HiGHS)
# ══════════════════════════════════════════════════════════════════════════════
# Algoritmo: Programación Lineal Entera Mixta (MILP)
# Solver: HiGHS via scipy.optimize.milp — solver de clase mundial, gratuito
# Función objetivo: MAXIMIZAR Net Value Total del Portafolio
# Restricciones: presupuesto por vigencia, unicidad, dependencias, no-discrecionales

try:
    from scipy.optimize import milp, LinearConstraint, Bounds
    import numpy as np
    MILP_AVAILABLE = True
except ImportError:
    MILP_AVAILABLE = False


@dataclass
class BudgetPeriod:
    year: int
    budget_COP: float


@dataclass
class PortfolioResult:
    selected: list        # [(project_id, year, net_value, capex)]
    postponed: list       # [(project_id, reason)]
    total_net_value: float
    total_capex: float
    solver_status: str
    config_version: str


def _preselect_by_eav(projects: list, engine: "ValueEngine"):
    """Para cada exclusion_group_id con más de una alternativa, se queda
    solo la de mayor Valor Anual Equivalente — salvo que una de las
    alternativas esté protegida (no-discrecional o con Gasto Comprometido
    ya firmado), en cuyo caso esa gana sin importar su EAV. Sin esto, el
    punto donde efectivamente se decide cuál alternativa de una necesidad
    se ejecuta compara net_value_COP crudo, que favorece sistemáticamente
    a la alternativa de vida más larga por pura acumulación — el EAV
    existe precisamente para evitarlo, pero hasta ahora no decidía nada.

    'Protegida' = is_mandatory O is_committed. Un proyecto con contrato ya
    firmado (is_committed) fuerza su ejecución vía R11 sin importar la
    optimización — descartarlo por EAV antes de que R11 llegue a
    protegerlo sería exactamente el mismo error que ya se corrigió para
    is_mandatory, con la agravante de que hay un contrato de por medio.

    Devuelve (ganadores, descartados). `descartados` es una lista de
    tuplas (proyecto_descartado, proyecto_ganador) — quien llame reporta
    la razón en el formato que le convenga a su propio resultado.

    No modifica exclusion_group_id ni ninguna otra restricción existente:
    la unicidad de grupo (R3d) sigue vigente en ambos optimizadores como
    salvaguarda, no se retira.
    """
    from collections import defaultdict as _dd
    grupos: dict = _dd(list)
    ganadores = []
    for p in projects:
        gid = getattr(p, "exclusion_group_id", None)
        if gid:
            grupos[gid].append(p)
        else:
            ganadores.append(p)

    descartados = []
    for gid, alternativas in grupos.items():
        if len(alternativas) == 1:
            ganadores.append(alternativas[0])
            continue
        protegidas = [p for p in alternativas if p.is_mandatory or getattr(p, "is_committed", False)]
        if len(protegidas) > 1:
            raise ValueError(
                f"Grupo de exclusión '{gid}': hay {len(protegidas)} alternativas "
                f"protegidas (no-discrecional o Gasto Comprometido) "
                f"({', '.join(p.id for p in protegidas)}). "
                f"Solo una alternativa del grupo puede ejecutarse — a lo sumo "
                f"una puede estar protegida."
            )
        if protegidas:
            ganador = protegidas[0]
        else:
            ganador = max(alternativas, key=lambda p: engine.evaluate(p)["eav_COP"])
        ganadores.append(ganador)
        descartados.extend((p, ganador) for p in alternativas if p is not ganador)

    return ganadores, descartados


class PortfolioOptimizer:
    """
    Optimizador MILP de portafolio multiperíodo.

    Variables de decisión: x[i,t] ∈ {0,1}
      x[i,t] = 1 → proyecto i se ejecuta en vigencia t

    Función objetivo:
      MAXIMIZAR Σ_i Σ_t [ x[i,t] × Net_Value(i,t) ]

    Restricciones:
      1. Presupuesto: Σ_i [ x[i,t] × CAPEX_i ] ≤ Cupo_t   ∀ t
      2. Unicidad:    Σ_t [ x[i,t] ] ≤ 1                    ∀ i
      3. Unicidad no-discrecional: Σ_t [ x[i,t] ] = 1       ∀ i en must-do
      4. Dependencia: x[j,t] ≤ Σ_{τ≤t} x[i,τ]              ∀ j que depende de i
    """

    def __init__(self, config: dict):
        self.config = config
        self.engine = ValueEngine(config)
        self._meta = config.get("_metadata", {})

    def optimize(self, projects: list, budget_periods: list,
                 base_year: int = None) -> PortfolioResult:

        if not MILP_AVAILABLE:
            return PortfolioResult(
                [], [], 0, 0,
                "ERROR: scipy no disponible. Instala con: pip install scipy",
                self._meta.get("version", "?")
            )

        if base_year is None:
            base_year = datetime.now().year

        # Preselección por EAV: reduce cada grupo de exclusión a una sola
        # alternativa ANTES de construir cualquier matriz — el resto del
        # método nunca ve las alternativas descartadas.
        projects, _preselect_descartados = _preselect_by_eav(projects, self.engine)

        n_proj = len(projects)
        n_years = len(budget_periods)
        year_map = {bp.year: t for t, bp in enumerate(budget_periods)}
        proj_ids = [p.id for p in projects]
        n_vars = n_proj * n_years

        # ── Net Value por proyecto × vigencia (CoD materializado si se posterga) ──
        net_values = np.zeros((n_proj, n_years))
        # Matriz CAPEX real por proyecto × vigencia (Copperleaf C55 §5.3)
        # capex_matrix[i, t] = CAPEX del proyecto i en la vigencia t.
        # Para proyectos con construction_years > 1, el CAPEX se distribuye
        # sobre múltiples vigencias. Si el proyecto no tiene distribución
        # explícita, el motor distribuye el CAPEX total uniformemente.
        capex_matrix = np.zeros((n_proj, n_years))
        capex_arr = np.zeros(n_proj)

        # capex_matrix[i, k] es RELATIVO: cuánto gasta el proyecto i, k años
        # después de cualquier candidato que se pruebe como arranque. La
        # Restricción 1 (más abajo) ya asume esta semántica al desplazarla
        # por t_rel = t-s; antes, el camino uniforme anclaba en el índice
        # del execution_year DECLARADO en vez de en 0, desplazándola dos
        # veces para cualquier candidato que no fuera el primer período del
        # plan. capex_by_year tenía el mismo problema, buscando además el
        # año calendario literal en vez de reanclarse.
        for i, proj in enumerate(projects):
            capex_arr[i] = proj.costs.capex_COP
            capex_by_year = getattr(proj.costs, 'capex_by_year', {})
            declared = proj.declared_execution_year or proj.execution_year
            if capex_by_year:
                # capex_arr[i] pasa a ser el total real del perfil — de lo
                # contrario "selected" reporta capex_COP=0 (el escalar sin
                # usar) para un proyecto de perfil explícito, aunque el
                # motor sí lo haya contabilizado bien internamente. No
                # afecta la rama uniforme (else, abajo): esta reasignación
                # solo corre cuando capex_by_year existe.
                capex_arr[i] = sum(capex_by_year.values())
                for year, monto in capex_by_year.items():
                    k = year - declared
                    if 0 <= k < n_years:
                        capex_matrix[i, k] += monto
            else:
                cy = max(1, proj.construction_years)
                for k in range(min(cy, n_years)):
                    capex_matrix[i, k] = capex_arr[i] / cy
            # Evaluar VNT para cada año de inicio posible.
            #
            # deferral_years se calcula y se pasa explícitamente — antes
            # este bucle llamaba evaluate(proj) sin él, que por defecto usa
            # deferral_years=0 SIEMPRE, sin importar qué candidato bp.year
            # se esté probando. Verificado empíricamente antes de esta
            # corrección: el VNT de un proyecto con CAPEX legado apenas
            # variaba entre años candidatos (~0,1-0,2% por año, no un
            # descuento económico real) — el escaneo de año óptimo de este
            # optimizador era casi inerte.
            #
            # CORRECCIÓN (hallada en la revisión final de todo el trabajo):
            # la primera versión de este cambio usaba el parámetro base_year
            # que optimize() recibe, afirmando —incorrectamente— que así
            # replicaba lo que ya hace EnterpriseOptimizer. No es cierto:
            # EnterpriseOptimizer calcula deferral = period.year - min(years)
            # (epm_core.py, bucle de precálculo de VNT), es decir, contra el
            # año MÁS TEMPRANO DE LAS VIGENCIAS RECIBIDAS, no contra el
            # parámetro base_year — ese parámetro ahí solo se usa para
            # Continuous Planning (EVM/ETC), nunca para deferral_years. Si un
            # llamador pasara budget_periods cuyo primer año no coincide con
            # base_year (p. ej. re-planeación a mitad de año), los dos
            # optimizadores habrían dado VNT distinto para el mismo proyecto
            # — verificado empíricamente: ~18% de diferencia en un caso de
            # prueba con esa combinación. Se corrige usando min(bp.year for
            # bp in budget_periods), la misma referencia que ya usa
            # EnterpriseOptimizer, en vez de base_year.
            _plan_base_year = min(bp.year for bp in budget_periods)
            for t, bp in enumerate(budget_periods):
                original_exec = proj.execution_year
                proj.execution_year = bp.year
                deferral = max(0, bp.year - _plan_base_year)
                r = self.engine.evaluate(proj, deferral_years=deferral)
                net_values[i, t] = r["net_value_COP"]
                proj.execution_year = original_exec

        # ── Función objetivo: minimizar negativo del Net Value ──
        c_obj = -net_values.flatten()

        A_rows, b_up, b_lo = [], [], []

        # Restricción 1: Presupuesto por vigencia — MILP multiperíodo Copperleaf
        # Usa capex_matrix real por vigencia, no CAPEX total.
        # Garantiza ejecutabilidad: el plan respeta el cupo de cada año.
        for t, bp in enumerate(budget_periods):
            row = np.zeros(n_vars)
            for i in range(n_proj):
                # Si el proyecto i inicia en la vigencia s, consume
                # capex_matrix[i, t-s] en la vigencia actual t
                for s in range(n_years):
                    t_rel = t - s   # posición relativa dentro de la construcción
                    if 0 <= t_rel < n_years and capex_matrix[i, t_rel] > 0:
                        row[i * n_years + s] += capex_matrix[i, t_rel]
            A_rows.append(row)
            b_up.append(bp.budget_COP)
            b_lo.append(-np.inf)

        # Restricción 2 & 3: Unicidad (discrecional ≤1, no-discrecional =1)
        for i, proj in enumerate(projects):
            row = np.zeros(n_vars)
            for t in range(n_years):
                row[i * n_years + t] = 1
            A_rows.append(row)
            if proj.is_mandatory:
                b_up.append(1); b_lo.append(1)     # Exactamente 1
            else:
                b_up.append(1); b_lo.append(0)     # A lo sumo 1

        # Restricción 4: Dependencias
        dep_index = {p.id: i for i, p in enumerate(projects)}
        for j, proj in enumerate(projects):
            for dep_id in proj.dependencies:
                i = dep_index.get(dep_id)
                if i is None:
                    continue
                for t in range(n_years):
                    row = np.zeros(n_vars)
                    row[j * n_years + t] = 1
                    for tau in range(t + 1):
                        row[i * n_years + tau] -= 1
                    A_rows.append(row)
                    b_up.append(0); b_lo.append(-np.inf)

        # ── Restricción 5: Exclusión mutua de grupo (exclusion_group_id) ──────
        # Réplica de R3d del optimizador enterprise. Sin ella este optimizador
        # podía seleccionar DOS alternativas de la misma necesidad —comprar el
        # transformador nuevo y además repotenciar el viejo— produciendo un plan
        # inejecutable que además sobreestima el valor del portafolio.
        #
        # Σ x[i,t] ≤ 1 sobre todo el grupo y todas las vigencias: se elige una
        # alternativa o ninguna. "Ninguna" es la inacción, y es una respuesta
        # legítima cuando ninguna alternativa compensa el riesgo del baseline.
        from collections import defaultdict as _dd
        _excl: dict = _dd(list)
        for i, proj in enumerate(projects):
            gid = getattr(proj, "exclusion_group_id", None)
            if gid:
                _excl[gid].append(i)
        for gid, idxs in _excl.items():
            if len(idxs) < 2:
                continue
            row = np.zeros(n_vars)
            for i in idxs:
                for t in range(n_years):
                    row[i * n_years + t] = 1
            A_rows.append(row)
            b_up.append(1); b_lo.append(-np.inf)

        # Coherencia con R2/R3: una alternativa marcada como no-discrecional
        # dentro de un grupo de exclusión vuelve infactible el problema, porque
        # unicidad exige =1 para ella y el grupo exige ≤1 para todos. Se detecta
        # aquí en vez de dejar que el solver devuelva INFEASIBLE sin explicación.
        for gid, idxs in _excl.items():
            if len(idxs) < 2:
                continue
            _mand = [projects[i].id for i in idxs if projects[i].is_mandatory]
            if len(_mand) > 1:
                raise ValueError(
                    f"Grupo de exclusión '{gid}': hay {len(_mand)} alternativas "
                    f"marcadas como no-discrecionales ({', '.join(_mand)}). "
                    f"Solo una alternativa del grupo puede ejecutarse — a lo sumo "
                    f"una puede ser obligatoria."
                )

        A = np.array(A_rows)
        constraints = LinearConstraint(A, b_lo, b_up)
        bounds = Bounds(lb=0, ub=1)
        integrality = np.ones(n_vars)

        result = milp(c_obj, constraints=constraints,
                      integrality=integrality, bounds=bounds)

        # ── Procesar resultado ──
        selected, postponed = [], []
        total_nv = 0.0
        total_capex = 0.0

        if result.success:
            x = result.x.reshape(n_proj, n_years)
            assigned = set()
            for i, proj in enumerate(projects):
                for t, bp in enumerate(budget_periods):
                    if x[i, t] > 0.5:
                        nv = net_values[i, t]
                        selected.append({
                            "project_id": proj.id,
                            "project_name": proj.name,
                            "year": bp.year,
                            "net_value_COP": nv,
                            "capex_COP": capex_arr[i],
                            "vei": nv / capex_arr[i] if capex_arr[i] > 0 else 0
                        })
                        total_nv += nv
                        total_capex += capex_arr[i]
                        assigned.add(proj.id)
            for proj in projects:
                if proj.id not in assigned:
                    postponed.append({
                        "project_id": proj.id,
                        "project_name": proj.name,
                        "reason": "Sin cupo presupuestal en el horizonte"
                    })
            selected.sort(key=lambda x: x["net_value_COP"], reverse=True)

        # Las descartadas por EAV se conocen ANTES de correr el solver — se
        # reportan sin importar si este tuvo éxito o no. Si quedaran dentro
        # del `if result.success:` de arriba, un solve infactible las haría
        # desaparecer en silencio, contradiciendo la garantía de la sección
        # 6 del spec ("no desaparecen en silencio, se reportan con la razón").
        for descartada, ganadora in _preselect_descartados:
            postponed.append({
                "project_id": descartada.id,
                "project_name": descartada.name,
                "reason": f"Descartada por EAV frente a {ganadora.id}",
            })

        return PortfolioResult(
            selected=selected,
            postponed=postponed,
            total_net_value=total_nv,
            total_capex=total_capex,
            solver_status=(normalize_solver_status(result.message)
                           if result.success else "INFEASIBLE"),
            config_version=self._meta.get("version", "?")
        )

    def print_portfolio_report(self, result: PortfolioResult,
                                budget_periods: list):
        section_header("OPTIMIZACIÓN DE PORTAFOLIO — MILP EXACTO (HiGHS)")
        print(c(f"  Solver: {result.solver_status}", Colors.GREEN))
        print(c(f"  Config: v{result.config_version}", Colors.DIM))
        print()

        # Resumen por vigencia
        by_year = {}
        for s in result.selected:
            y = s["year"]
            if y not in by_year: by_year[y] = []
            by_year[y].append(s)

        for bp in budget_periods:
            projs = by_year.get(bp.year, [])
            used = sum(p["capex_COP"] for p in projs)
            pct = used/bp.budget_COP*100 if bp.budget_COP > 0 else 0
            print(c(f"  Vigencia {bp.year} — Cupo: {cop(bp.budget_COP)} | "
                    f"Usado: {cop(used)} ({pct:.1f}%)", Colors.BOLD))
            if projs:
                print(f"  {'Proyecto':<40} {'Net Value':>22} {'CAPEX':>18} {'VEI':>7}")
                print(c("  " + "─" * 92, Colors.DIM))
                for p in projs:
                    print(f"  {p['project_name']:<40} {cop(p['net_value_COP']):>22} "
                          f"{cop(p['capex_COP']):>18} {p['vei']:>6.2f}x")
            else:
                print(c("  (ningún proyecto asignado)", Colors.DIM))
            print()

        print(c("  ══ TOTALES DEL PORTAFOLIO ══", Colors.BOLD + Colors.GREEN))
        print(f"  Net Value Total   : {cop(result.total_net_value)}")
        print(f"  CAPEX Total       : {cop(result.total_capex)}")
        print(f"  Proyectos activos : {len(result.selected)}")

        if result.postponed:
            print()
            print(c(f"  Proyectos postergados ({len(result.postponed)}):", Colors.YELLOW))
            for p in result.postponed:
                print(c(f"    • {p['project_id']} — {p['project_name']}: {p['reason']}", Colors.DIM))


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN H: SENSITIVITY ANALYST
# ══════════════════════════════════════════════════════════════════════════════

class SensitivityAnalyst:
    """
    Análisis What-If: impacto de cambios en parámetros sobre el ranking.
    Compara escenario base vs. escenario modificado sin alterar el config original.
    """

    def __init__(self, config: dict):
        self.base_config = config
        self.engine_base = ValueEngine(config)

    def run_scenario(self, projects: list, adjustments: dict,
                     scenario_name: str = "Escenario What-If") -> dict:
        """
        adjustments: dict de cambios al config o multiplicadores de proyecto.

        Claves soportadas:
          wacc                  — tasa de descuento (float)
          analysis_horizon_years / horizon_override — horizonte global (int)
          capex_multiplier      — factor sobre CAPEX de cada proyecto (float, ej. 1.15)
          benefit_multiplier    — factor sobre VD_benefit de cada proyecto (float, ej. 0.90)
          risk_multiplier       — factor sobre ΔR de cada proyecto (float, ej. 1.25)
          capex_override        — CAPEX fijo para todos los proyectos en COP (float)
        """
        mod_config = copy.deepcopy(self.base_config)
        self._apply_adjustments(mod_config, adjustments)
        engine_mod = ValueEngine(mod_config)

        # Multiplicadores a nivel de proyecto (no van en config)
        overrides = mod_config.pop("_scenario_overrides", {})
        capex_mult    = overrides.get("capex_multiplier",   1.0)
        benefit_mult  = overrides.get("benefit_multiplier", 1.0)
        risk_mult     = overrides.get("risk_multiplier",    1.0)
        capex_fixed   = overrides.get("capex_override",     None)

        def _apply_proj_overrides(proj):
            """Retorna copia del proyecto con multiplicadores aplicados."""
            needs_copy = (capex_mult != 1.0 or benefit_mult != 1.0
                          or risk_mult != 1.0 or capex_fixed is not None)
            if not needs_copy:
                return proj
            p = copy.deepcopy(proj)
            # CAPEX
            if capex_fixed is not None:
                p.costs.capex_COP = float(capex_fixed)
            elif capex_mult != 1.0:
                p.costs.capex_COP *= capex_mult
                if p.costs.capex_by_year:
                    p.costs.capex_by_year = {
                        yr: v * capex_mult for yr, v in p.costs.capex_by_year.items()
                    }
            # Beneficio — se escala delta_annual de cada sub-criterio
            if benefit_mult != 1.0:
                for drv in (p.drivers or []):
                    for sc in (drv.sub_criteria or []):
                        # SubCriterionInput: delta_annual es cantidad física o nivel CR
                        if hasattr(sc, "delta_annual"):
                            sc.delta_annual = sc.delta_annual * benefit_mult
                        elif isinstance(sc, dict):
                            for k in ("delta_annual", "annual_benefit_COP", "quantity"):
                                if k in sc:
                                    sc[k] = sc[k] * benefit_mult
            # Riesgo — se escala consecuencia del input de riesgo
            if risk_mult != 1.0 and p.risk:
                p.risk.consequence_level = min(
                    5.0, p.risk.consequence_level * risk_mult
                )
            return p

        base_results, mod_results = [], []
        for proj in projects:
            rb = self.engine_base.evaluate(proj)
            rm = engine_mod.evaluate(_apply_proj_overrides(proj))
            base_results.append({"id": proj.id, "name": proj.name,
                                  "net_value": rb["net_value_COP"],
                                  "vei": rb["value_efficiency_index"]})
            mod_results.append({"id": proj.id, "name": proj.name,
                                 "net_value": rm["net_value_COP"],
                                 "vei": rm["value_efficiency_index"]})

        base_ranked = sorted(base_results, key=lambda x: x["net_value"], reverse=True)
        mod_ranked  = sorted(mod_results,  key=lambda x: x["net_value"], reverse=True)
        base_rank_map = {r["id"]: i+1 for i, r in enumerate(base_ranked)}
        mod_rank_map  = {r["id"]: i+1 for i, r in enumerate(mod_ranked)}

        comparison = []
        for b, m in zip(
            sorted(base_results, key=lambda x: x["id"]),
            sorted(mod_results,  key=lambda x: x["id"])
        ):
            delta_nv = m["net_value"] - b["net_value"]
            rank_change = base_rank_map[b["id"]] - mod_rank_map[b["id"]]
            comparison.append({
                "id": b["id"], "name": b["name"],
                "base_net_value": b["net_value"], "mod_net_value": m["net_value"],
                "delta_net_value": delta_nv,
                "base_rank": base_rank_map[b["id"]], "mod_rank": mod_rank_map[b["id"]],
                "rank_change": rank_change
            })

        return {
            "scenario_name": scenario_name,
            "adjustments": adjustments,
            "comparison": comparison
        }

    def _apply_adjustments(self, config: dict, adjustments: dict):
        """Aplica ajustes al config o almacena multiplicadores de proyecto.

        Claves de config (efecto inmediato en ValueEngine):
          wacc                  — tasa de descuento
          analysis_horizon_years / horizon_override — horizonte de análisis

        Claves de proyecto (almacenadas en _scenario_overrides para run_scenario):
          capex_multiplier      — factor multiplicador sobre CAPEX (ej. 1.15 = +15%)
          benefit_multiplier    — factor sobre beneficios anuales (ej. 0.90 = -10%)
          risk_multiplier       — factor sobre nivel de consecuencia de riesgo
          capex_override        — CAPEX absoluto en COP (reemplaza, no multiplica)
        """
        proj_override_keys = {
            "capex_multiplier", "benefit_multiplier", "risk_multiplier", "capex_override"
        }
        for key, value in adjustments.items():
            if key == "wacc":
                config["wacc"] = value
            elif key in ("analysis_horizon_years", "horizon_override"):
                config["analysis_horizon_years"] = value
            elif key in proj_override_keys:
                config.setdefault("_scenario_overrides", {})[key] = value
            else:
                # Intento genérico: clave plana en raíz del config
                config[key] = value

    def print_sensitivity_report(self, scenario: dict):
        section_header(f"SENSIBILIDAD — {scenario['scenario_name']}")
        print(c(f"  Ajustes aplicados: {scenario['adjustments']}", Colors.YELLOW))
        print()
        print(f"  {'Proyecto':<35} {'ΔNet Value':>20} {'Rango Base':>12} {'Rango Mod':>10} {'Δ Rango':>8}")
        print(c("  " + "─" * 90, Colors.DIM))
        for row in sorted(scenario["comparison"], key=lambda x: abs(x["delta_net_value"]), reverse=True):
            delta_str = f"{'+' if row['delta_net_value'] >= 0 else ''}{cop(row['delta_net_value'])}"
            rank_str  = f"{'+' if row['rank_change'] > 0 else ''}{row['rank_change']}" if row['rank_change'] != 0 else "="
            color = Colors.GREEN if row["delta_net_value"] >= 0 else Colors.RED
            print(f"  {row['name']:<35} {c(delta_str, color):>28} "
                  f"{row['base_rank']:>12} {row['mod_rank']:>10} {c(rank_str, color):>16}")


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN I: REPORTE Y TRAZABILIDAD
# ══════════════════════════════════════════════════════════════════════════════

def print_report(result: dict, config_version: str = "?"):
    """Reporte de consola de una evaluación.

    Tolerante a resultados parciales: imprimir es una operación de salida y no
    debe abortar por un campo ausente. Los faltantes se muestran como '?'.
    """
    capex = result.get("capex_COP", 0.0) or 0.0
    nv    = result.get("net_value_COP", 0.0) or 0.0
    vei   = result.get("value_efficiency_index", 0.0) or 0.0
    ben   = result.get("benefits_raw_COP", 0.0) or 0.0

    # Clasificación
    if nv > 2 * capex:   priority = c("●  ALTA PRIORIDAD — Net Value > 2× CAPEX", Colors.GREEN + Colors.BOLD)
    elif nv > capex:     priority = c("●  PRIORIDAD MEDIA — Net Value 1-2× CAPEX", Colors.YELLOW + Colors.BOLD)
    elif nv > 0:         priority = c("●  PRIORIDAD BAJA — Net Value < 1× CAPEX", Colors.YELLOW)
    else:                priority = c("●  DESTRUYE VALOR — Net Value negativo", Colors.RED + Colors.BOLD)

    print()
    print(c("═" * 80, Colors.CYAN))
    print(c("  RESULTADO DE EVALUACIÓN — EPM MODELO DE DECISIÓN DE INVERSIÓN", Colors.BOLD))
    print(c("═" * 80, Colors.CYAN))
    print()
    print(f"  Proyecto  : {result.get('project_name','?')}")
    print(f"  ID        : {result.get('project_id','?')}")
    print(f"  Negocio   : {result.get('business_unit','?')}  |  Tipo: {result.get('investment_type','?')}")
    print(f"  Modelo    : {result.get('model_level','?')}  |  Config v{config_version}")
    print(f"  Horizonte : {result.get('analysis_horizon_years','?')} años  |  WACC: {(result.get('wacc') or 0)*100:.1f}%")
    print(f"  Factor NPV: {(result.get('factor_npv') or 0):.4f}")
    print()

    print(c("  ┌─ SÍNTESIS EJECUTIVA ─────────────────────────────────────────────────────", Colors.DIM))
    print(f"  {priority}")
    print()
    print(f"  Net Value              : {c(cop(nv), Colors.GREEN if nv >= 0 else Colors.RED)}")
    print(f"  VEI                    : {c(f'{vei:.2f}×', Colors.GREEN if vei >= 1 else Colors.RED)}")
    print()
    print(f"  Beneficios brutos      :  {cop(ben)}")
    print(f"  CAPEX (consumo valor)  : {cop(result['costs']['capex_COP'] if 'costs' in result else -(result.get('cost_npv_COP') or 0))}")
    print(f"  Costo neto NPV         :  {cop((result.get('cost_npv_COP') or 0))}")
    print()

    timing = result.get("timing_analysis") or {}
    if timing.get("delay_years", 0) > 0:
        print(c(f"  ⚠ TARDÍO +{timing.get('delay_years', 0)} año(s) vs. óptimo", Colors.YELLOW))
        print(f"  Cost of Delay total    : {cop(timing.get('cod_total_COP', 0))}")
        print(f"  CoD mensual            : {cop(timing.get('cod_monthly_COP', 0))}/mes")
    else:
        print(c("  ✓ Timing óptimo", Colors.GREEN))
    print(c("  └────────────────────────────────────────────────────────────────────────", Colors.DIM))

    print()
    print(c("  DESGLOSE POR VALUE DRIVER", Colors.BOLD))
    print(c("  " + "─" * 72, Colors.DIM))
    print(f"  {'ID':<8} {'Reto':<38} {'Valor Neto (COP)':>22}")
    print(c("  " + "─" * 72, Colors.DIM))
    v_total = max(abs(nv), 1)
    for dr in result.get("driver_results") or []:
        v = dr.get('value_COP',0)
        bar_len = int(min(18, abs(v) / v_total * 18))
        bar = c("█" * bar_len, Colors.GREEN if v >= 0 else Colors.RED)
        print(f"  {c(dr.get('id','?'), Colors.YELLOW):<15} {dr.get('reto','?'):<38} {cop(v):>22}  {bar}")

    if result.get("risk_analysis"):
        ra = result.get("risk_analysis") or {}
        print()
        print(c("  RIESGO MITIGADO", Colors.BOLD))
        print(c("  " + "─" * 72, Colors.DIM))
        print(f"  Riesgo Caso Base (anual)  : {cop(ra['baseline_annual_COP'])}")
        print(f"  Riesgo Residual (anual)   : {cop(ra['residual_annual_COP'])}")
        print(f"  ΔR Mitigado (NPV)         : {c(cop(ra['delta_r_npv_COP']), Colors.GREEN)}")

    # ── Análisis de Tiempo Óptimo (si está disponible) ──────────────────────
    ot = result.get("optimal_timing")
    if ot:
        print()
        print(c("  ANÁLISIS DE TEMPORIZACIÓN ÓPTIMA (Copperleaf Economic End-of-Life)", Colors.BOLD))
        print(c("  " + "─" * 72, Colors.DIM))

        urgency_color = {
            "AHORA":    Colors.RED + Colors.BOLD,
            "PRONTO":   Colors.YELLOW + Colors.BOLD,
            "FLEXIBLE": Colors.GREEN
        }.get(ot.get("urgency", "FLEXIBLE"), Colors.DIM)

        print(f"  Urgencia              : {c(ot.get('urgency','?'), urgency_color)}")
        print(f"  Año óptimo de inicio  : {c(str(ot['optimal_year']), Colors.GREEN + Colors.BOLD)}")
        print(f"  VNT en año óptimo     : {c(cop(ot['optimal_net_value']), Colors.GREEN)}")
        print(f"  VNT si ejecuta hoy    : {cop(ot['t0_net_value'])}")
        delta = ot.get("delta_vs_t0", 0)
        delta_str = f"{'+' if delta >= 0 else ''}{cop(delta)}"
        delta_color = Colors.GREEN if delta >= 0 else Colors.RED
        print(f"  Diferencia vs. hoy    : {c(delta_str, delta_color)}")
        print()
        print(c("  Curva de Valor Neto por año de inicio:", Colors.DIM))
        print(f"  {'Año':>6} {'Delay':>6} {'ΔR Anual':>18} {'CAPEX Ajustado':>18} {'VNT':>20} {'VEI':>7}")
        print(c("  " + "─" * 82, Colors.DIM))
        opt_year = ot["optimal_year"]
        for yr in ot.get("yearly_results", []):
            is_opt = yr["year"] == opt_year
            marker = c(" ◄ ÓPTIMO", Colors.GREEN + Colors.BOLD) if is_opt else ""
            row_color = Colors.GREEN if is_opt else Colors.DIM if yr["net_value_COP"] < 0 else ""
            line = (f"  {yr['year']:>6} {yr['delay_years']:>5}a "
                    f"{cop(yr['delta_r_annual_COP']):>18} "
                    f"{cop(yr['capex_ajustado_COP']):>18} "
                    f"{cop(yr['net_value_COP']):>20} "
                    f"{yr['vei']:>6.2f}x")
            print(c(line, row_color) + marker)
        print()

        # Recomendación ejecutiva
        delay = ot.get("optimal_delay_years", 0)
        if delay == 0:
            rec = (f"Intervenir en {ot['optimal_year']}. "
                   f"Postergar un año reduce el VNT en {cop(abs(ot['yearly_results'][1]['net_value_COP'] - ot['optimal_net_value']))} "
                   f"por degradación del activo y aumento de CAPEX.")
        else:
            rec = (f"Esperar {delay} año(s) hasta {ot['optimal_year']}. "
                   f"El VNT mejora {cop(abs(delta))} vs. ejecutar hoy. "
                   f"La curva de riesgo aún no justifica la intervención inmediata.")
        print(c(f"  ► Recomendación: {rec}", Colors.CYAN))

    print()
    print(c("═" * 80, Colors.CYAN))


def write_audit_log(result: dict, config: dict, path: str = None):
    """ISO 55001 §4.5 — registro inmutable de cada evaluación.

    Tolerante a resultados incompletos: la traza de auditoría no puede ser la
    razón por la que se pierde una evaluación válida. Los campos ausentes se
    registran como '?' en lugar de abortar.

    Args:
        path: Destino explícito del registro. Si es None, escribe
              epm_audit.log junto a este módulo. Existe para que las pruebas
              puedan ejercitar la función sin ensuciar la traza de producción:
              una corrida de pruebas no debe generar asientos de auditoría.
    """
    meta = config.get("_metadata", {})
    version = meta.get("version", "?")
    log_path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "epm_audit.log")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _nv  = result.get("net_value_COP")
    _vei = result.get("value_efficiency_index")
    line = (f"{ts} | config v{version} | {result.get('project_id','?')} | "
            f"{result.get('project_name','?')} | "
            f"net_value: {f'{_nv:,.0f}' if isinstance(_nv,(int,float)) else '?'} COP | "
            f"VEI: {f'{_vei:.2f}x' if isinstance(_vei,(int,float)) else '?'} | "
            f"tipo: {result.get('investment_type','?')} | "
            f"negocio: {result.get('business_unit','?')}\n")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN J: MENÚ PRINCIPAL UNIFICADO
# ══════════════════════════════════════════════════════════════════════════════

def load_config(path: str = None) -> dict:
    """Carga la configuración del motor desde un archivo JSON.

    Args:
        path: Ruta explícita al config.json. Si es None, busca config.json
              en el mismo directorio que este módulo (comportamiento por defecto).

    Returns:
        dict con la configuración cargada, o DEFAULT_CONFIG si no se encuentra.
    """
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    config_path = path
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        merged, added = _merge_config_defaults(cfg)
        meta = merged.get("_metadata", {})
        print(c(f"  ✓ config.json v{meta.get('version','?')} cargado — "
                f"{meta.get('last_updated','?')} — aprobado: {meta.get('approved_by','?')}",
                Colors.GREEN))
        if added:
            print(c(f"  ℹ {len(added)} parámetro(s) ausentes en el archivo tomados de "
                    f"DEFAULT_CONFIG: {', '.join(sorted(added)[:6])}"
                    f"{' …' if len(added) > 6 else ''}", Colors.YELLOW))
        return merged
    print(c("  ℹ DEFAULT_CONFIG — coloca config.json junto a epm.py para parámetros calibrados.",
            Colors.YELLOW))
    return copy.deepcopy(DEFAULT_CONFIG)


def _merge_config_defaults(cfg: dict) -> Tuple[dict, List[str]]:
    """Completa el config del archivo con lo que falte de DEFAULT_CONFIG.

    Por qué existe: un config.json guardado con una versión anterior del motor
    NO tiene las claves que se agregaron después. Sin esta fusión, una capacidad
    nueva queda silenciosamente desactivada en toda instalación con un archivo
    viejo — el `.get(clave, {})` devuelve vacío y el motor sigue como si nada.
    Es un modo de falla silencioso: el parámetro no está, nadie se entera.

    El archivo SIEMPRE gana sobre el default: la fusión solo agrega ausentes,
    nunca sobrescribe una calibración existente.

    Returns:
        (config fusionado, lista de rutas de clave que se tomaron del default)
    """
    added: List[str] = []

    def _deep(dst: dict, src: dict, prefix: str = "") -> dict:
        for k, v in src.items():
            ruta = f"{prefix}{k}"
            if k not in dst:
                dst[k] = copy.deepcopy(v)
                added.append(ruta)
            elif isinstance(v, dict) and isinstance(dst.get(k), dict):
                _deep(dst[k], v, prefix=f"{ruta}.")
        return dst

    merged = _deep(copy.deepcopy(cfg), DEFAULT_CONFIG)
    return merged, added


def save_config(config: dict, path: str = None):
    """Persiste la configuración del motor en un archivo JSON.

    Args:
        config: dict de configuración a guardar.
        path:   Ruta explícita de destino. Si es None, guarda config.json
                en el mismo directorio que este módulo.
    """
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    config["_metadata"]["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(c(f"  ✓ config.json guardado en {path}.", Colors.GREEN))


def _modulo_evaluacion(config: dict):
    engine = ValueEngine(config)
    wizard = ProjectWizard(config)
    while True:
        project = wizard.run()
        if project:
            result = engine.evaluate(project)
            print_report(result, config.get("_metadata", {}).get("version", "?"))
            write_audit_log(result, config)
        again = input(c("\n  ¿Evaluar otro proyecto? [s/N]: ", Colors.DIM)).strip().lower()
        if again != "s":
            break


def _modulo_intervenciones(config: dict):
    engine_int = InterventionEngine(config)
    wizard = ProjectWizard(config)
    section_header("OPCIONES DE INTERVENCIÓN")
    need_id    = ask("ID de la necesidad (ej. NEED-TYD-001)")
    need_label = ask("Descripción de la necesidad")
    n_options  = ask("¿Cuántas opciones vas a comparar? [2-5]", cast=int, default=2) or 2
    options = []
    for i in range(n_options):
        print(c(f"\n  ── OPCIÓN {i+1} ──", Colors.BOLD))
        opt_id    = ask(f"ID opción {i+1} (ej. OPT-A)")
        opt_label = ask(f"Descripción opción {i+1}")
        print(c("  Ahora ingresa los datos del proyecto para esta opción:", Colors.DIM))
        project = wizard.run()
        if project:
            options.append(InterventionOption(opt_id, opt_label, project))
    if len(options) >= 2:
        decision = engine_int.evaluate_need(need_id, need_label, options)
        engine_int.print_need_report(decision)
        for opt in decision.options_evaluated:
            write_audit_log(opt["result"], config)


def _modulo_optimizador(config: dict):
    engine = ValueEngine(config)
    wizard = ProjectWizard(config)
    section_header("OPTIMIZADOR DE PORTAFOLIO — MILP")

    if not MILP_AVAILABLE:
        print(c("  ✗ scipy no disponible. Instala con: pip install scipy", Colors.RED))
        return

    # Ingresar proyectos
    projects = []
    print(c("  Ingresa los proyectos del portafolio (Enter en blanco para terminar):", Colors.DIM))
    while True:
        print(c(f"\n  Proyecto {len(projects)+1}:", Colors.BOLD))
        resp = input(c("  ¿Agregar proyecto? [S/n]: ", Colors.CYAN)).strip().lower()
        if resp == "n":
            break
        project = wizard.run()
        if project:
            projects.append(project)

    if len(projects) < 2:
        print(c("  Se necesitan al menos 2 proyectos para optimizar.", Colors.YELLOW))
        return

    # Ingresar vigencias y cupos
    print()
    n_years = ask("¿Cuántas vigencias incluir en el horizonte? [1-5]", cast=int, default=3) or 3
    budget_periods = []
    current_year = datetime.now().year
    for t in range(n_years):
        year = current_year + t
        budget = ask(f"  Cupo de inversión {year} (COP)", cast=float)
        budget_periods.append(BudgetPeriod(year=year, budget_COP=budget))

    # Optimizar
    optimizer = PortfolioOptimizer(config)
    result = optimizer.optimize(projects, budget_periods)
    optimizer.print_portfolio_report(result, budget_periods)


def _modulo_sensibilidad(config: dict):
    analyst = SensitivityAnalyst(config)
    wizard = ProjectWizard(config)
    section_header("ANÁLISIS DE SENSIBILIDAD — WHAT-IF")

    # Cargar proyectos base
    projects = []
    print(c("  Ingresa los proyectos a analizar:", Colors.DIM))
    while True:
        resp = input(c(f"\n  ¿Agregar proyecto {len(projects)+1}? [S/n]: ", Colors.CYAN)).strip().lower()
        if resp == "n":
            break
        p = wizard.run()
        if p:
            projects.append(p)

    if not projects:
        return

    # Definir escenario
    print()
    scenario_name = ask("Nombre del escenario (ej. 'WACC 12%')")
    print(c("  Ajustes disponibles:", Colors.DIM))
    print(c("    wacc                   → ej. 0.12", Colors.DIM))
    print(c("    analysis_horizon_years → ej. 15", Colors.DIM))
    adjustments = {}
    while True:
        key = input(c("  Parámetro a cambiar (Enter para terminar): ", Colors.CYAN)).strip()
        if not key:
            break
        val_raw = ask(f"  Nuevo valor para {key}", cast=float)
        adjustments[key] = val_raw

    if adjustments:
        scenario = analyst.run_scenario(projects, adjustments, scenario_name)
        analyst.print_sensitivity_report(scenario)


def _ver_config(config: dict):
    section_header("CONFIG.JSON ACTIVO")
    meta = config.get("_metadata", {})
    print(f"  Versión      : {meta.get('version','?')}")
    print(f"  Autor        : {meta.get('author','?')}")
    print(f"  Actualizado  : {meta.get('last_updated','?')}")
    print(f"  Aprobado por : {meta.get('approved_by','?')}")
    print(f"  WACC         : {config.get('wacc',0)*100:.1f}%")
    print(f"  Horizonte    : {config.get('analysis_horizon_years',10)} años")
    thresh = config.get("proportionality_thresholds_COP", {})
    print(f"  Proporcionalidad (por Unidad de Negocio):")
    bus_list = [k for k in thresh if not k.startswith("_") and isinstance(thresh[k], dict)]
    if bus_list:
        for bu in bus_list:
            bt = thresh[bu]
            full_v = bt.get("full_model_COP")
            simpl_v = bt.get("simplified_model_COP")
            fstr = f"COP {full_v/1e9:.1f}B" if full_v else "Pendiente ASPD"
            sstr = f"COP {simpl_v/1e9:.1f}B" if simpl_v else "Pendiente ASPD"
            print(f"    {bu:<14}  Full >= {fstr}  |  Simplified >= {sstr}")
    else:
        print(f"    Full model       : {thresh.get('full_model') or 'Pendiente ASPD'}")
        print(f"    Simplified model : {thresh.get('simplified_model') or 'Pendiente ASPD'}")
        print(f"    Rule-based       : {thresh.get('rule_based') or 'Pendiente ASPD'}")
    print()
    print(c("  Value Drivers activos:", Colors.BOLD))
    for vd in config.get("value_drivers", []):
        print(f"    {vd['id']} — {vd['reto']} ({len(vd['sub_criteria'])} sub-criterios)")


def main():
    print()
    print(c("═" * 70, Colors.GREEN))
    _v = DEFAULT_CONFIG.get("_metadata", {}).get("version", "?")
    print(c(f"  EPM — MODELO DE DECISIÓN DE INVERSIÓN  v{_v}", Colors.BOLD + Colors.GREEN))
    print(c("  Copperleaf C55 · ISO 55001:2024 · IAM SSG", Colors.DIM))
    print(c("  ASPD — Gerencia de Planeación Empresarial", Colors.DIM))
    print(c("═" * 70, Colors.GREEN))
    print()

    config = load_config()

    while True:
        print()
        print(c("  MENÚ PRINCIPAL", Colors.BOLD))
        print(c("  " + "─" * 55, Colors.DIM))
        print(c("  [1]", Colors.YELLOW) +
              "  Evaluar proyecto individual        " +
              c("Wizard guiado → Net Value + VEI", Colors.DIM))
        print(c("  [2]", Colors.YELLOW) +
              "  Comparar opciones de intervención  " +
              c("Opción A vs B vs C por necesidad", Colors.DIM))
        print(c("  [3]", Colors.YELLOW) +
              "  Optimizar portafolio (MILP)         " +
              c("Máximo Net Value con cupos CAPEX", Colors.DIM))
        print(c("  [4]", Colors.YELLOW) +
              "  Análisis de sensibilidad What-If    " +
              c("Impacto de parámetros en ranking", Colors.DIM))
        print(c("  [5]", Colors.YELLOW) +
              "  Ver configuración activa            " +
              c("Parámetros y versión del config", Colors.DIM))
        print(c("  [6]", Colors.YELLOW) +
              "  Ver log de trazabilidad             " +
              c("Historial ISO 55001 §4.5", Colors.DIM))
        print(c("  [0]", Colors.DIM) + "  Salir")
        print()

        opcion = input(c("  Selecciona una opción: ", Colors.CYAN)).strip()

        if   opcion == "1": _modulo_evaluacion(config)
        elif opcion == "2": _modulo_intervenciones(config)
        elif opcion == "3": _modulo_optimizador(config)
        elif opcion == "4": _modulo_sensibilidad(config)
        elif opcion == "5": _ver_config(config)
        elif opcion == "6": _ver_log()
        elif opcion == "0": print(c("  Hasta luego.", Colors.DIM)); break
        else: print(c("  Opción no válida.", Colors.RED))


def _ver_log():
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "epm_audit.log")
    if not os.path.exists(log_path):
        print(c("  No hay evaluaciones registradas aún.", Colors.DIM))
        return
    section_header("LOG DE TRAZABILIDAD — ISO 55001 §4.5")
    with open(log_path, encoding="utf-8") as f:
        lines = f.readlines()
    last = lines[-20:] if len(lines) > 20 else lines
    print(c(f"  Mostrando últimas {len(last)} de {len(lines)} entradas:", Colors.DIM))
    print()
    for line in last:
        print(c("  " + line.strip(), Colors.DIM))


if __name__ == "__main__":
    main()






# ── numpy (opcional — para curvas de degradación) ─────────────────────────────
try:
    import numpy as np
    NP_OK = True
except ImportError:
    NP_OK = False



# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO A — ENTERPRISE MILP OPTIMIZER (PuLP)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ResourcePeriod:
    """Restricción de personal/recursos por vigencia."""
    year: int
    budget_COP: float
    max_person_months: float = float('inf')   # Capacidad máxima en persona-mes
    max_projects_concurrent: int = 999         # Máximo de proyectos ejecutándose en paralelo
    max_risk_COP: float = float('inf')         # Riesgo acumulado máximo tolerable en el portafolio

    # ── Restricción TOTEX por vigencia ────────────────────────────
    totex_budget_COP: Optional[float] = None
    """Techo TOTEX (CAPEX + NPV(OPEX incremental)) para la vigencia.
    Si es None, no se aplica restricción TOTEX (comportamiento previo).
    Ingresado por el analista — los datos de OPEX histórico que normalmente
    provienen de SIGEAS se ingresan manualmente en el campo opex_incremental
    de cada ProjectInput.
    R12: Σ_i(CAPEX_i + OPEX_NPV_i) × x[i][t] ≤ totex_budget_COP"""


@dataclass
class EnterprisePortfolioResult:
    """Resultado extendido del optimizador Enterprise."""
    selected: List[dict]
    postponed: List[dict]
    infeasible: List[dict]                    # Proyectos que generan infactibilidad
    total_net_value: float
    total_capex: float
    solver_status: str
    solver_name: str
    objective_value: float
    gap_pct: float                            # MIP gap en %
    solve_time_s: float
    config_version: str
    # Utilización por vigencia
    budget_utilization: Dict[int, dict]       # {year: {used, available, pct}}
    resource_utilization: Dict[int, dict]     # {year: {used_pm, available_pm, pct}}
    risk_utilization: Dict[int, dict]         # {year: {risk_COP, max_COP}}
    # Valor marginal de las restricciones.
    #   "Presupuesto_marginal" → valor marginal del cupo, por diferencia finita
    #     sobre la frontera. Es el único precio sombra válido para un MILP.
    #   "relajLP::<nombre>"    → dual de la relajación LP. REFERENCIAL: sirve
    #     para ver qué restricciones están activas, no como valor marginal del
    #     problema entero (la dualidad LP no se sostiene con variables enteras).
    shadow_prices: Dict[str, float]
    # Frontera eficiente — tratamiento de la incertidumbre presupuestal.
    # [{budget_pct, budget_COP, vnt_COP, capex_COP, n_projects,
    #   marginal_value, is_baseline, is_knee}]
    efficient_frontier: List[dict]
    # ── Continuous Planning ────────────────────────────────────────
    completed: List[dict] = field(default_factory=list)
    """Proyectos ya ejecutados (pct_complete=1). Excluidos del MILP; incluidos
    aquí para visibilidad del presupuesto total comprometido y métricas EVM."""
    actuals_summary: dict = field(default_factory=dict)
    """Resumen de las métricas EVM del portafolio en ejecución:
    {spi_portfolio, cpi_portfolio, n_completed, n_in_progress, budget_consumed_COP}"""

    # ── Outage-Aligned Scheduling ───────────────────────────────────
    outage_schedule: List[dict] = field(default_factory=list)
    """Alineamiento outage→proyecto resuelto por el MILP.
    Cada entrada: {project_id, project_name, asset_id, year_selected,
                   outage_id, outage_name, valid_years, r10_active, warning}
    r10_active=True  → restricción R10 fue aplicada
    warning          → mensaje si require_outage_alignment=True pero sin ventana"""

    # ── Committed Spend ────────────────────────────────────────────
    committed_summary: dict = field(default_factory=dict)
    """Resumen del gasto comprometido en el portafolio:
    {n_committed, capex_committed_COP, capex_discretionary_COP,
     pct_committed, committed_projects: [{id, name, year, capex}]}
    Permite al analista ver qué fracción del presupuesto ya está comprometida
    antes de que el MILP asigne el cupo discrecional."""

    # ── TOTEX ───────────────────────────────────────────────────────
    totex_utilization: Dict[int, dict] = field(default_factory=dict)
    """{year: {capex_COP, opex_npv_COP, totex_COP, budget_COP, pct}}
    Desglose TOTEX = CAPEX + NPV(OPEX) por vigencia.
    Permite identificar si el portafolio es TOTEX-eficiente o CAPEX-pesado."""

    totex_frontier: List[dict] = field(default_factory=list)
    """Frontera eficiente TOTEX: [{totex_pct, vnt_COP, n_projects, capex, opex_npv}]
    Curva VNT vs. techo TOTEX (análogo a efficient_frontier pero en TOTEX).
    Vacía si compute_totex_frontier=False."""

    degradation_hints_applied: List[dict] = field(default_factory=list)
    """Proyectos cuyo timing fue influenciado por el DegradationEngine.
    Cada entrada: {project_id, asset_id, hint_year, actual_year, bonus_pct}"""


# ══════════════════════════════════════════════════════════════════════════════
# MOTOR DE DEPENDENCIAS TEMPORALES — Copperleaf C55 §7.4
# ══════════════════════════════════════════════════════════════════════════════

class DependencyError(Exception):
    """Error en la configuración de dependencias del portafolio."""
    pass


class DependencyEngine:
    """
    Motor de Dependencias Temporales para el Optimizador Enterprise.

    Implementa las Precedence Constraints de Copperleaf C55 §7.4 con tres
    capacidades adicionales sobre la restricción simple de `dependencies`:

    1. Restricción de Precedencia Temporal con Lag
       sum_{k=1}^{t} x_hijo(k) <= sum_{k=1}^{t-lag} x_padre(k)
       El hijo no puede iniciar en t a menos que el padre haya sido
       seleccionado en t-lag o antes.

    2. Validación de Dependencias Circulares (Deadlock Detection)
       Detecta A→B→A antes de iniciar el MILP. Lanza DependencyError.

    3. Gestión de Exclusión Mutua con Validación
       Proyectos excluyentes (alternativas al mismo problema) no pueden
       coexistir en el portafolio. Valida que no se creen dependencias
       entre proyectos excluyentes (lo que sería un deadlock lógico).

    4. Efecto Cascada de Valor (CoD Cascade)
       Cuando el padre se posterga, su CoD se incrementa con el CoD
       de todos sus hijos (proporcional al cascade_cod_weight del config).

    Uso:
        dep_engine = DependencyEngine(config)
        dep_engine.validate(projects)  # lanza DependencyError si hay problemas
        cascade_vnt = dep_engine.apply_cascade_cod(projects, engine)
        # luego pasar cascade_vnt al optimizador en lugar de vnt normal
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        dep_cfg = self.config.get("dependency_engine", {})
        self.cascade_enabled  = dep_cfg.get("cascade_cod_enabled", True)
        self.cascade_weight   = float(dep_cfg.get("cascade_cod_weight", 1.0))
        self.deadlock_check   = dep_cfg.get("deadlock_check_enabled", True)
        self.mutex_symmetric  = dep_cfg.get("mutual_exclusion_symmetric", True)
        self.max_depth        = int(dep_cfg.get("max_dependency_depth", 5))

    # ── Helpers internos ─────────────────────────────────────────────────────

    def _build_graph(self, projects: List) -> Dict[str, str]:
        """Construye mapa hijo→padre desde precedent_project_id."""
        return {
            p.id: p.precedent_project_id
            for p in projects
            if getattr(p, "precedent_project_id", "")
        }

    def _build_multigraph(self, projects: List) -> Dict[str, List[str]]:
        """Mapa hijo→[padres] uniendo los DOS mecanismos de precedencia.

        El modelo tiene dos campos para el mismo concepto: `precedent_project_id`
        (uno solo, lo usan el optimizador enterprise y este motor) y
        `dependencies` (lista, la usa el optimizador de scipy). La detección de
        ciclos cubría únicamente el primero, así que un ciclo expresado por
        `dependencies` llegaba intacto al MILP.
        """
        g: Dict[str, List[str]] = {}
        for p in projects:
            padres = []
            prec = getattr(p, "precedent_project_id", "")
            if prec:
                padres.append(prec)
            for dep in (getattr(p, "dependencies", None) or []):
                if dep and dep not in padres:
                    padres.append(dep)
            if padres:
                g[p.id] = padres
        return g

    def _build_children(self, projects: List) -> Dict[str, List[str]]:
        """Construye mapa padre→[hijos]."""
        children: Dict[str, List[str]] = {}
        for p in projects:
            pid = getattr(p, "precedent_project_id", "")
            if pid:
                children.setdefault(pid, []).append(p.id)
        return children

    def _all_ids(self, projects: List) -> set:
        return {p.id for p in projects}

    # ── 1. Validación de Deadlocks ───────────────────────────────────────────

    def _detect_cycle(self, node: str, graph: Dict[str, List[str]],
                       visited: set, stack: set) -> Optional[str]:
        """DFS para detectar ciclos. Retorna el nodo que cierra el ciclo o None.

        Recorre TODOS los padres de cada nodo, no solo el primero: un proyecto
        puede tener un precedente y además varias dependencias.
        """
        visited.add(node)
        stack.add(node)
        for parent in graph.get(node, []):
            if not parent:
                continue
            if parent in stack:
                stack.discard(node)
                return parent
            if parent not in visited:
                cycle = self._detect_cycle(parent, graph, visited, stack)
                if cycle:
                    stack.discard(node)
                    return cycle
        stack.discard(node)
        return None

    def _check_deadlocks(self, projects: List) -> None:
        """Detecta dependencias circulares. Lanza DependencyError si encuentra."""
        graph = self._build_multigraph(projects)
        visited, stack = set(), set()
        for node in list(graph):
            if node not in visited:
                cycle_node = self._detect_cycle(node, graph, visited, stack)
                if cycle_node:
                    raise DependencyError(
                        f"Dependencia circular detectada: el proyecto '{node}' "
                        f"crea un ciclo que pasa por '{cycle_node}'. "
                        f"Revisa los campos precedent_project_id y dependencies "
                        f"del portafolio."
                    )

    # ── 2. Validación de Exclusión Mutua vs Dependencias ────────────────────

    def _check_mutex_vs_precedence(self, projects: List) -> None:
        """
        Valida que no existan dependencias entre proyectos excluyentes.
        Si A excluye a B pero B es padre de A (o viceversa), es un deadlock lógico:
        A no puede ejecutarse sin B, pero no pueden coexistir.
        """
        mutex_map: Dict[str, set] = {}
        for p in projects:
            excl = set(getattr(p, "mutual_exclusion_ids", []) or [])
            if excl:
                mutex_map[p.id] = excl

        graph = self._build_graph(projects)
        for child_id, parent_id in graph.items():
            if not parent_id:
                continue
            # ¿El padre está en la lista de excluyentes del hijo?
            if child_id in mutex_map and parent_id in mutex_map[child_id]:
                raise DependencyError(
                    f"Deadlock lógico: el proyecto '{child_id}' depende de "
                    f"'{parent_id}' (precedent_project_id), pero ambos son "
                    f"mutuamente excluyentes (mutual_exclusion_ids). "
                    f"Un proyecto no puede requerir a su excluyente como prerrequisito."
                )
            # ¿El hijo está en la lista de excluyentes del padre?
            if parent_id in mutex_map and child_id in mutex_map[parent_id]:
                raise DependencyError(
                    f"Deadlock lógico: '{parent_id}' excluye a '{child_id}', "
                    f"pero '{child_id}' depende de '{parent_id}'. Inconsistencia."
                )

    # ── 3. Validación de profundidad ─────────────────────────────────────────

    def _check_depth(self, projects: List) -> List[str]:
        """
        Calcula la profundidad de cada cadena de dependencias.
        Retorna lista de advertencias para cadenas que superan max_depth.
        """
        graph = self._build_graph(projects)
        warnings = []

        def depth_of(node: str, visited: set) -> int:
            if node in visited:
                return 0  # ciclo ya detectado antes
            visited.add(node)
            parent = graph.get(node, "")
            return 1 + depth_of(parent, visited) if parent else 0

        for p in projects:
            if getattr(p, "precedent_project_id", ""):
                d = depth_of(p.id, set())
                if d > self.max_depth:
                    warnings.append(
                        f"Advertencia: proyecto '{p.id}' tiene una cadena de "
                        f"{d} dependencias (máx. recomendado: {self.max_depth})"
                    )
        return warnings

    # ── 4. Simetría automática de exclusión mutua ────────────────────────────

    def _apply_mutex_symmetry(self, projects: List) -> None:
        """
        Si A.mutual_exclusion_ids contiene B, añade A a B.mutual_exclusion_ids.
        Modifica los proyectos in-place.
        """
        if not self.mutex_symmetric:
            return
        mutex_map: Dict[str, set] = {}
        for p in projects:
            mutex_map[p.id] = set(getattr(p, "mutual_exclusion_ids", []) or [])

        # Primera pasada: expandir simetría
        for pid, excl_set in list(mutex_map.items()):
            for other_id in list(excl_set):
                mutex_map.setdefault(other_id, set()).add(pid)

        # Aplicar de vuelta a los proyectos
        pid_to_proj = {p.id: p for p in projects}
        for pid, excl_set in mutex_map.items():
            if pid in pid_to_proj:
                pid_to_proj[pid].mutual_exclusion_ids = list(excl_set)

    # ── 5. Cascade CoD ───────────────────────────────────────────────────────

    def apply_cascade_cod(
        self,
        projects: List,
        engine,                          # ValueEngine
        vnt_matrix: List[List[float]],   # vnt[i][t] ya calculado
        years: List[int],
    ) -> List[List[float]]:
        """
        Aplica el Efecto Cascada de Valor: cuando el padre se posterga,
        su VNT ajustado incluye el CoD de sus hijos.

        Retorna una copia de vnt_matrix con los valores del padre incrementados
        por el CoD de sus hijos ponderado por cascade_cod_weight.

        Fundamento: si postponer el padre bloquea al hijo, el costo real del
        retraso del padre no es solo su propio CoD sino también el valor
        que el hijo deja de generar mientras espera.
        """
        if not self.cascade_enabled:
            return vnt_matrix

        import copy
        vnt_adj = copy.deepcopy(vnt_matrix)
        children = self._build_children(projects)
        pid_to_idx = {p.id: i for i, p in enumerate(projects)}

        for parent_id, child_ids in children.items():
            pi = pid_to_idx.get(parent_id)
            if pi is None:
                continue
            for child_id in child_ids:
                ci = pid_to_idx.get(child_id)
                if ci is None:
                    continue
                child_proj = projects[ci]
                # Calcular CoD del hijo para cada año de inicio del padre
                lag = max(0, getattr(child_proj, "min_lag_years", 0))
                for t in range(len(years)):
                    # Si el padre inicia en t, el hijo solo puede iniciar en t+lag
                    # El CoD del hijo = valor_t+lag - valor_t (lo que pierde por esperar)
                    t_hijo_earliest = min(t + lag, len(years) - 1)
                    if t_hijo_earliest < len(years):
                        cod_hijo = max(0, vnt_matrix[ci][t] - vnt_matrix[ci][t_hijo_earliest])
                        vnt_adj[pi][t] -= cod_hijo * self.cascade_weight

        return vnt_adj

    # ── Validación master ────────────────────────────────────────────────────

    def validate(self, projects: List) -> List[str]:
        """
        Ejecuta todas las validaciones de dependencias.
        Lanza DependencyError si hay errores críticos.
        Retorna lista de advertencias no críticas.
        """
        all_ids = self._all_ids(projects)
        warnings = []

        # Validar que los padres referenciados existan en el portafolio
        for p in projects:
            pid = getattr(p, "precedent_project_id", "")
            if pid and pid not in all_ids:
                raise DependencyError(
                    f"Proyecto '{p.id}' referencia a padre '{pid}' "
                    f"que no existe en el portafolio."
                )
            for excl_id in getattr(p, "mutual_exclusion_ids", []) or []:
                if excl_id not in all_ids:
                    warnings.append(
                        f"Advertencia: '{p.id}'.mutual_exclusion_ids contiene "
                        f"'{excl_id}' que no existe en el portafolio."
                    )

        if self.deadlock_check:
            self._check_deadlocks(projects)
        self._check_mutex_vs_precedence(projects)
        self._apply_mutex_symmetry(projects)
        warnings.extend(self._check_depth(projects))

        return warnings

    def precedence_summary(self, projects: List) -> str:
        """Retorna un resumen legible de las cadenas de dependencias."""
        children = self._build_children(projects)
        pid_map = {p.id: p.name for p in projects}
        roots = [
            p for p in projects
            if not getattr(p, "precedent_project_id", "")
            and p.id in children
        ]
        lines = ["Cadenas de Precedencia:"]
        def print_chain(pid, indent=0):
            name = pid_map.get(pid, pid)
            lines.append("  " * indent + f"{'└─ ' if indent else ''}{pid} [{name}]")
            for child_id in children.get(pid, []):
                child_proj = next((p for p in projects if p.id == child_id), None)
                lag = getattr(child_proj, "min_lag_years", 0) if child_proj else 0
                lag_str = f" (lag={lag}a)" if lag > 0 else ""
                lines.append("  " * (indent+1) + f"→ {child_id}{lag_str} [{pid_map.get(child_id, child_id)}]")
                print_chain(child_id, indent + 2)
        for r in roots:
            print_chain(r.id)
        return "\n".join(lines)


class EnterpriseOptimizer:
    """
    Optimizador MILP Enterprise usando PuLP + CBC.
    Carga config desde archivo externo (config.json) si se proporciona.

    Mejoras sobre el optimizador scipy:

    1. Restricciones de personal (persona-mes por vigencia)
       Cada proyecto consume recursos según su tamaño y duración.
       Permite modelar la capacidad real del equipo de ejecución.

    2. Restricciones de riesgo máximo del portafolio
       El riesgo acumulado de los proyectos seleccionados no puede
       superar un umbral definido por vigencia. Evita que el portafolio
       concentre riesgo de ejecución en un solo año.

    3. Restricciones de concurrencia
       Máximo de proyectos ejecutándose simultáneamente en una vigencia.
       Refleja limitaciones reales de gestión de cartera.

    4. Variables duales (shadow prices)
       Por cada restricción activa, el modelo reporta cuánto VNT adicional
       se gana si se relaja esa restricción en 1 unidad.
       Herramienta de negociación presupuestal ante la Junta Directiva.

    5. Frontera Eficiente automática
       Corre el MILP para 10 niveles de presupuesto (10%..100%) y
       construye la curva VNT vs CAPEX sin intervención manual.
    """

    # Estimación persona-mes por rango de CAPEX (COP MM)
    # Tabla calibrada con benchmarks de EPM — revisión anual ASPD
    PM_TABLE = [
        (0,         1_000,    2.0),    # < $1.000MM → 2 p-m
        (1_000,    10_000,    6.0),    # $1.000-10.000MM → 6 p-m
        (10_000,   50_000,   15.0),    # $10.000-50.000MM → 15 p-m
        (50_000,  200_000,   35.0),    # $50.000-200.000MM → 35 p-m
        (200_000, float('inf'), 80.0), # > $200.000MM → 80 p-m
    ]

    @staticmethod
    def load_config(config_path: str) -> dict:
        """Carga config desde archivo JSON externo (config.json)."""
        import json
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def __init__(self, config: dict = None, config_path: str = None):
        """
        Args:
            config:      dict de configuración (DEFAULT_CONFIG o cargado externamente)
            config_path: ruta a config.json — tiene precedencia sobre config
        """
        if config_path:
            self.config = self.load_config(config_path)
        elif config is not None:
            self.config = config
        else:
            self.config = DEFAULT_CONFIG
        self.engine = ValueEngine(self.config)
        self._meta = self.config.get("_metadata", {})
        self.dep_engine = DependencyEngine(self.config)

    def _estimate_person_months(self, capex_MM: float) -> float:
        """Estima persona-mes necesarios según CAPEX. Parametrizable."""
        for lo, hi, pm in self.PM_TABLE:
            if lo <= capex_MM < hi:
                return pm
        return 80.0

    @staticmethod
    def _risk_matrix_level(consequence: int, probability: int) -> int:
        """
        Calcula el nivel de riesgo (1-5) en la matriz 5×5 estándar EPM/Copperleaf.

        Matriz (filas = probabilidad P, columnas = consecuencia C):
          P\\C  1   2   3   4   5
          5  [ 3,  4,  4,  5,  5 ]
          4  [ 2,  3,  4,  4,  5 ]
          3  [ 2,  2,  3,  4,  4 ]
          2  [ 1,  2,  2,  3,  3 ]
          1  [ 1,  1,  2,  2,  3 ]

        Colores EPM: 1=Verde, 2=Amarillo, 3=Naranja, 4=Rojo, 5=Crítico.
        Zona inaceptable estándar: nivel ≥ 4 (Rojo/Crítico).
        """
        c = max(1, min(5, int(consequence)))
        p = max(1, min(5, int(probability)))
        _MATRIX = {
            (1,1):1,(2,1):1,(3,1):2,(4,1):2,(5,1):3,
            (1,2):1,(2,2):2,(3,2):2,(4,2):3,(5,2):3,
            (1,3):2,(2,3):2,(3,3):3,(4,3):4,(5,3):4,
            (1,4):2,(2,4):3,(3,4):4,(4,4):4,(5,4):5,
            (1,5):3,(2,5):4,(3,5):4,(4,5):5,(5,5):5,
        }
        return _MATRIX.get((c, p), 1)

    def optimize(
        self,
        projects: List[ProjectInput],
        periods: List[ResourcePeriod],
        base_year: int = None,
        solver_time_limit_s: int = 120,
        mip_gap: float = 0.01,
        compute_shadow_prices: bool = True,
        compute_frontier: bool = False,
        # compute_frontier: la frontera cuesta n_points × max_pct solves extra
        #   (≈15 corridas del MILP con los valores por defecto). Por eso NO se
        #   enciende aquí: haría que toda llamada interna —re-optimización,
        #   barridos, robustez, comparación de escenarios— pagara ese costo.
        #   Quien la necesita la pide explícitamente: la corrida principal de la
        #   aplicación la solicita, las corridas secundarias no.
        frontier_n_points: int = 10,
        frontier_max_pct: float = 1.5,
        # ── Alcances de optimización ───────────────────────────────
        bu_filter: Optional[str] = None,
        # bu_filter: Modo SILO — filtra el portafolio a una sola BU.
        #   'TyD'|'Generacion'|'AyA'|'Gas'|'Todos'|None(global)
        pisos_inversion_COP: Optional[Dict[str, float]] = None,
        # pisos_inversion_COP: Modo ENTERPRISE GLOBAL — inversión mínima por BU.
        #   {'TyD': 500e9, 'Generacion': 200e9, 'AyA': 150e9}
        #   Restricción R7: Σ_t CAPEX(i∈BU) ≥ piso_BU — evita canibalización.
        risk_tolerance_level: int = 4,
        # risk_tolerance_level: Nivel de riesgo INACEPTABLE (1-5, escala EPM).
        #   R8: Para cada asset_id con baseline_risk_level ≥ este valor,
        #   el MILP FUERZA la selección de ≥1 proyecto que baje el riesgo
        #   por debajo del umbral. Shadow price = costo de no mitigar.
        #   4 = Zona Roja (estándar EPM). 5 = solo Críticos. 6 = desactiva R8.
        actuals: Optional[List["ProjectActual"]] = None,
        # actuals: Lista de ProjectActual con gastos reales y % de avance.
        #   Continuous Planning (RFI §5 fila 143)
        #   pct_complete = 1.0 → completado: excluido del MILP, AC es sunk cost.
        #   0 < pct < 1.0    → en curso: x fijado a 1 en su año, capex = ETC.
        #   None (default)   → modo clásico sin actuals (comportamiento previo).
        planned_outages: Optional[List["PlannedOutage"]] = None,
        # planned_outages: Calendario corporativo de ventanas de mantenimiento.
        #   Outage-Aligned Scheduling (RFI §3/§4)
        #   Proyectos con require_outage_alignment=True solo pueden ejecutarse
        #   en años que tengan ≥1 PlannedOutage con el mismo asset_id.
        #   El cruce es automático via asset_id (Opción 3 — sin outage_ids explícitos).
        #   None (default)   → sin restricción de ventana (comportamiento previo).
        degradation_hints: Optional[Dict[str, int]] = None,
        # degradation_hints: — Mapa asset_id → año óptimo de intervención
        #   derivado del AssetDegradationEngine.analyze() (campo optimal_intervention_year).
        #   El analista corre el análisis de degradación por activo en el Módulo B y puede
        #   traspasar los resultados al MILP con este parámetro.
        #   Efecto: bono de timing del 5% de VNT en el año-hint (preferencia suave,
        #   no hard constraint). El solver puede ignorarla si otra vigencia es mejor.
        #   None (default)   → sin preferencia de timing por degradación.
        compute_totex_frontier: bool = False,
        # compute_totex_frontier: — genera la curva VNT vs. techo TOTEX.
        #   Corre el MILP 8 veces barriendo TOTEX en [50%, 150%] del TOTEX baseline.
        #   False (default)  → sin frontera TOTEX (comportamiento previo).
    ) -> EnterprisePortfolioResult:

        if not PULP_OK:
            raise RuntimeError("PuLP no instalado. Ejecuta: pip install pulp")

        import time
        t0 = time.time()

        if base_year is None:
            base_year = datetime.now().year

        # ── SILO: filtrar proyectos a una sola BU ───────────────────────
        if bu_filter:
            projects = [p for p in projects if p.business_unit == bu_filter]
            if not projects:
                raise ValueError(
                    f"Modo Silo: no hay proyectos para la BU '{bu_filter}'. "
                    "Verifica el portafolio cargado.")

        # Preselección por EAV: reduce cada grupo de exclusión a una sola
        # alternativa ANTES de construir cualquier matriz.
        projects, _preselect_descartados = _preselect_by_eav(projects, self.engine)

        # ── Continuous Planning — clasificar actuals ────────────────
        # Referencia: RFI §5 fila 143 — planificación continua y re-optimización.
        # Tres estados posibles de cada proyecto con actuals:
        #   COMPLETADO  pct = 1.0 → AC sunk, x[i][t] = 0 para todo t
        #   EN CURSO    0 < pct < 1 → x fijado al año de ejecución, capex = ETC
        #   PLANEADO    sin actual  → comportamiento normal del MILP
        _actual_map:      Dict[str, "ProjectActual"] = {}
        _evm_by_idx:      Dict[int, "EVMResult"]     = {}
        _completed_idxs:  set = set()   # índices de proyectos ya terminados
        _in_progress_idxs: set = set()  # índices de proyectos en ejecución parcial

        if actuals:
            _actual_map = {a.project_id: a for a in actuals}

        n = len(projects)
        T = len(periods)
        years = [p.year for p in periods]
        year_idx = {y: t for t, y in enumerate(years)}

        # ── Pre-calcular VNT(i,t) y CAPEX(i,t) ──────────────────────────────
        vnt   = [[0.0]*T for _ in range(n)]     # vnt[i][t]
        capex_matrix = [[0.0]*T for _ in range(n)]  # capex real por vigencia
        pm_req = [0.0]*n                          # persona-mes por proyecto

        for i, proj in enumerate(projects):
            cap_total = proj.costs.capex_COP
            cap_mm = cap_total / 1e6
            pm_req[i] = self._estimate_person_months(cap_mm)

            cap_by_year = getattr(proj.costs, 'capex_by_year', {}) or {}
            declared = proj.declared_execution_year or proj.execution_year
            for t, period in enumerate(periods):
                # capex_matrix[i][t] es UN SOLO número por candidato (no un
                # ledger multi-año) — igual que la rama uniforme, que solo
                # reporta cap_total/cy (un año de gasto), esta rama reporta
                # el tramo declarado para el año de arranque (offset 0),
                # reanclado. Antes: buscaba period.year literal en
                # cap_by_year, así que para cualquier candidato que no
                # fuera exactamente el año declarado, reportaba 0 aunque el
                # proyecto sí tuviera costo real ahí.
                if cap_by_year:
                    # A diferencia de la rama uniforme (que reporta cap_total/cy,
                    # un año de gasto), aquí se reporta el TOTAL del perfil —
                    # reportar solo el tramo del año declarado subestimaba
                    # proyectos multi-tramo de la misma forma parcial que el
                    # defecto original, solo que menos severa. Asimetría
                    # preexistente entre las dos ramas, documentada aquí, no
                    # armonizada — fuera de alcance de esta corrección.
                    capex_matrix[i][t] = sum(cap_by_year.values())
                else:
                    cy = max(1, proj.construction_years)
                    capex_matrix[i][t] = cap_total / cy

                # VNT si inicia en este año (: incluir años de postergación)
                original = proj.execution_year
                proj.execution_year = period.year
                deferral = max(0, period.year - min(years))  # años de espera respecto al año base del plan
                try:
                    r = self.engine.evaluate(proj, deferral_years=deferral)
                    vnt[i][t] = r["net_value_COP"]
                except Exception:
                    vnt[i][t] = -cap_total  # penalización si falla
                proj.execution_year = original

        # ── Clasificar proyectos con actuals y ajustar capex ──────
        if _actual_map:
            for i, proj in enumerate(projects):
                act = _actual_map.get(proj.id)
                if act is None:
                    continue
                evm = EVMEngine.compute(proj, act, base_year)
                _evm_by_idx[i] = evm

                if act.pct_complete >= 1.0:
                    _completed_idxs.add(i)
                    # Completados: zeroise capex_matrix → x fijado a 0 en R9
                    for t in range(T):
                        capex_matrix[i][t] = 0.0
                elif act.pct_complete > 0:
                    _in_progress_idxs.add(i)
                    # En curso: reemplazar capex con ETC distribuido en años restantes
                    cy       = max(1, proj.construction_years)
                    exec_yr  = proj.execution_year
                    end_yr   = exec_yr + cy - 1         # último año de construcción
                    rem_yrs  = max(1, sum(
                        1 for yr in years if base_year <= yr <= end_yr))
                    etc_per_yr = evm.etc_COP / rem_yrs
                    for t in range(T):
                        yr = years[t]
                        capex_matrix[i][t] = etc_per_yr if base_year <= yr <= end_yr else 0.0

        # ── Modelo PuLP ───────────────────────────────────────────────────────
        prob = pulp.LpProblem("EPM_AIP_Enterprise", pulp.LpMaximize)

        # Variables de decisión: x[i][t] ∈ {0,1}
        x = [[pulp.LpVariable(f"x_{i}_{t}", cat="Binary")
              for t in range(T)] for i in range(n)]

        # ── PRE-OPTIMIZACIÓN: Validar dependencias y CASCADE CoD ─────────────
        dep_warnings = self.dep_engine.validate(projects)
        for w in dep_warnings:
            print(f"  ⚠ {w}")

        # Cascade CoD: VNT del padre absorbe el CoD de hijos retrasados.
        # Debe calcularse ANTES de establecer la función objetivo para que
        # el solver maximice el VNT real incluyendo el efecto cascada.
        vnt_cascade = self.dep_engine.apply_cascade_cod(
            projects, self.engine, vnt, years)

        # Función objetivo: maximizar VNT total con CoD cascada (C55 §7.4)
        prob += pulp.lpSum(vnt_cascade[i][t] * x[i][t]
                           for i in range(n) for t in range(T)), "VNT_total"

        # ── R1: Presupuesto por vigencia ──────────────────────────────────────
        # Antes: si cap_by_year no cubría TODOS los años de la ventana [0,cy),
        # los años sin declarar caían al reparto uniforme del total —
        # cobrando dos veces. Y el año calendario declarado nunca se
        # reanclaba al candidato s que el MILP está probando: un proyecto
        # con capex_by_year solo podía "verse" gastando en los años
        # calendario literales que el analista escribió.
        for t, period in enumerate(periods):
            expr = []
            for i in range(n):
                proj = projects[i]
                cy = max(1, proj.construction_years)
                cap_by_year = getattr(proj.costs, 'capex_by_year', {}) or {}
                declared = proj.declared_execution_year or proj.execution_year
                for s in range(T):
                    if cap_by_year:
                        # Perfil explícito: se reancla completo al candidato s.
                        # Un año de la ventana sin tramo declarado consume 0 —
                        # nunca cae al reparto uniforme del total.
                        delta = years[s] - declared
                        coef = sum(
                            monto for year_k, monto in cap_by_year.items()
                            if year_k + delta == years[t]
                        )
                    else:
                        t_rel = t - s
                        coef = proj.costs.capex_COP / cy if 0 <= t_rel < cy else 0.0
                    if coef > 0:
                        expr.append(coef * x[i][s])
            if expr:
                prob += (pulp.lpSum(expr) <= period.budget_COP,
                         f"Presupuesto_{period.year}")

        # ── R2: Unicidad — cada proyecto a lo sumo en una vigencia ───────────
        # NOTA: se añade índice i para garantizar nombres únicos incluso si
        # hay proyectos con IDs duplicados en el portafolio.
        for i, proj in enumerate(projects):
            # Sanitizar el ID para PuLP (eliminar caracteres especiales)
            safe_id = "".join(c if c.isalnum() or c == "_" else "_"
                              for c in str(proj.id))
            if proj.is_mandatory:
                prob += (pulp.lpSum(x[i][t] for t in range(T)) == 1,
                         f"Must_do_{i}_{safe_id}")
            else:
                prob += (pulp.lpSum(x[i][t] for t in range(T)) <= 1,
                         f"Unicidad_{i}_{safe_id}")

        # ── R3a: Dependencias simples (dependencies — solo portafolio, sin lag) ─
        dep_idx = {p.id: i for i, p in enumerate(projects)}
        for j, proj in enumerate(projects):
            for dep_id in (proj.dependencies or []):
                i = dep_idx.get(dep_id)
                if i is None:
                    continue
                # El padre debe estar seleccionado en ALGUNA vigencia
                safe_j = "".join(c if c.isalnum() or c=="_" else "_" for c in str(proj.id))
                safe_i = "".join(c if c.isalnum() or c=="_" else "_" for c in str(dep_id))
                prob += (
                    pulp.lpSum(x[j][t] for t in range(T))
                    <= pulp.lpSum(x[i][t] for t in range(T)),
                    f"Dep_simple_{j}_{safe_j}_{safe_i}"
                )

        # ── R3b: Precedencia Temporal con Lag (Copperleaf C55 §7.4) ──────────
        # Restricción: sum_{k=1}^{t} x_hijo(k) <= sum_{k=1}^{t-lag} x_padre(k)
        # El hijo no puede iniciar en t a menos que el padre haya sido
        # seleccionado en t-lag o antes.
        for j, proj in enumerate(projects):
            pid = getattr(proj, "precedent_project_id", "")
            if not pid:
                continue
            i = dep_idx.get(pid)
            if i is None:
                continue
            lag = max(0, getattr(proj, "min_lag_years", 0))
            for t in range(T):
                # Suma acumulada del hijo hasta t
                hijo_cumsum = pulp.lpSum(x[j][k] for k in range(t + 1))
                # Suma acumulada del padre hasta (t - lag)
                # Si t - lag < 0, el padre no pudo haber sido seleccionado aún → hijo = 0
                safe_j2 = "".join(c if c.isalnum() or c=="_" else "_" for c in str(proj.id))
                safe_p2 = "".join(c if c.isalnum() or c=="_" else "_" for c in str(pid))
                if t - lag < 0:
                    prob += (
                        hijo_cumsum <= 0,
                        f"Prec_lag_{j}_{safe_j2}_{safe_p2}_t{t}"
                    )
                else:
                    padre_cumsum = pulp.lpSum(x[i][k] for k in range(t - lag + 1))
                    prob += (
                        hijo_cumsum <= padre_cumsum,
                        f"Prec_lag_{j}_{safe_j2}_{safe_p2}_t{t}"
                    )

        # ── R3c: Exclusión Mutua ──────────────────────────────────────────────
        # Si A y B son excluyentes: sum_t(x_A(t)) + sum_t(x_B(t)) <= 1
        # (Solo uno puede estar en el portafolio, en cualquier año)
        processed_mutex = set()
        for j, proj in enumerate(projects):
            excl_ids = getattr(proj, "mutual_exclusion_ids", []) or []
            for excl_id in excl_ids:
                pair = tuple(sorted([proj.id, excl_id]))
                if pair in processed_mutex:
                    continue
                processed_mutex.add(pair)
                k = dep_idx.get(excl_id)
                if k is None:
                    continue
                safe_jm = "".join(c if c.isalnum() or c=="_" else "_" for c in str(proj.id))
                safe_em = "".join(c if c.isalnum() or c=="_" else "_" for c in str(excl_id))
                prob += (
                    pulp.lpSum(x[j][t] for t in range(T))
                    + pulp.lpSum(x[k][t] for t in range(T)) <= 1,
                    f"Mutex_{j}_{safe_jm}_{safe_em}"
                )

        # ── R3d: Exclusión Mutua de Grupo (exclusion_group_id) ───────────
        # Para cada grupo: solo puede seleccionarse UNA alternativa del grupo,
        # o NINGUNA (Status Quo si ninguna compensa el riesgo del baseline).
        # Restricción: Σ x_i(t) ≤ 1 para todos i en el mismo exclusion_group_id.
        # Diferencia vs R3c (mutual_exclusion_ids): este es N-way de grupo,
        # R3c es par-a-par. Ambos pueden coexistir sin conflicto.
        from collections import defaultdict
        excl_groups: dict = defaultdict(list)
        for i, proj in enumerate(projects):
            gid = getattr(proj, "exclusion_group_id", None)
            if gid:
                excl_groups[gid].append(i)
        processed_groups: set = set()
        for gid, group_indices in excl_groups.items():
            if gid in processed_groups or len(group_indices) < 2:
                continue
            processed_groups.add(gid)
            safe_gid = "".join(c if c.isalnum() or c == "_" else "_" for c in str(gid))
            # Suma de variables de TODOS los proyectos del grupo en TODOS los años ≤ 1
            prob += (
                pulp.lpSum(
                    x[i][t]
                    for i in group_indices
                    for t in range(T)
                ) <= 1,
                f"ExclGroup_{safe_gid}"
            )
            # Nota: ≤ 1 (no == 1) permite Status Quo (no elegir ninguna) cuando
            # ninguna alternativa tiene VNT marginal positivo vs. el baseline.

        # ── R7: Pisos de Inversión por BU (Enterprise Global) ─────────
        # Para cada BU con piso definido, el CAPEX TOTAL asignado a proyectos de
        # esa BU en TODO el horizonte debe superar el piso mínimo acordado.
        # Restricción: Σ_i∈BU Σ_t (CAPEX_i × x_i(t)) ≥ piso_BU
        # Esto garantiza que ningún negocio quede sub-financiado por el optimizer.
        if pisos_inversion_COP:
            for bu_key, piso in pisos_inversion_COP.items():
                if piso <= 0:
                    continue
                # Índices de proyectos de esta BU
                bu_indices = [i for i, p in enumerate(projects)
                              if p.business_unit == bu_key]
                if not bu_indices:
                    continue
                safe_bu = "".join(c if c.isalnum() else "_" for c in bu_key)
                # Suma de CAPEX asignado a proyectos de esta BU en todo el horizonte
                capex_bu_expr = pulp.lpSum(
                    projects[i].costs.capex_COP * x[i][t]
                    for i in bu_indices
                    for t in range(T)
                )
                prob += (capex_bu_expr >= piso,
                         f"PisoInversion_{safe_bu}")

        # ── R8: Must-Mitigate Riesgos Inaceptables ( — C55 §6.3) ──────
        # Para cada asset_id con baseline_risk_level ≥ risk_tolerance_level,
        # al menos un proyecto que lleve el riesgo a nivel aceptable debe ejecutarse.
        # Copperleaf: "Solucion de menor costo que mitigue todos los riesgos inaceptables".
        # Shadow price de MustMitigate_* = costo sombra de no poder mitigar.
        # Si no existe ningún proyecto mitigante en el portafolio → warning (no constraint).
        _r8_warnings: list = []
        _r8_forced: list = []
        if risk_tolerance_level <= 5:
            # Mapa: asset_id → {baseline_level, [índices de proyectos mitigantes]}
            _asset_mit_map: dict = {}
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
                if aid not in _asset_mit_map:
                    _asset_mit_map[aid] = {"baseline_level": bl_level, "mit_idxs": []}
                # El proyecto mitiga el riesgo si: baseline ≥ umbral Y residual < umbral
                if bl_level >= risk_tolerance_level and re_level < risk_tolerance_level:
                    _asset_mit_map[aid]["mit_idxs"].append(i)

            for aid, data in _asset_mit_map.items():
                bl_lvl   = data["baseline_level"]
                mit_idxs = data["mit_idxs"]
                if bl_lvl < risk_tolerance_level:
                    continue  # riesgo ya aceptable — no requiere mitigación forzada
                safe_aid = "".join(c if c.isalnum() else "_" for c in str(aid))
                if not mit_idxs:
                    _r8_warnings.append(
                        f"R8 WARNING — Activo '{aid}' tiene riesgo nivel {bl_lvl} "
                        f"(≥ tolerancia {risk_tolerance_level}) pero NO hay proyectos "
                        f"mitigantes en el portafolio. Riesgo quedará inaceptable."
                    )
                else:
                    prob += (
                        pulp.lpSum(x[i][t] for i in mit_idxs for t in range(T)) >= 1,
                        f"MustMitigate_{safe_aid}"
                    )
                    _r8_forced.append({
                        "asset_id": aid,
                        "baseline_level": bl_lvl,
                        "mitigation_projects": [projects[i].id for i in mit_idxs],
                    })
                    print(f"  [R8] Activo '{aid}' — riesgo L{bl_lvl} → "
                          f"forzando ≥1 de {[projects[i].id for i in mit_idxs]}")

        for w in _r8_warnings:
            print(f"  ⚠ {w}")

        # ── R4: Personal / persona-mes por vigencia ───────────────────────────
        for t, period in enumerate(periods):
            if period.max_person_months < float('inf'):
                prob += (
                    pulp.lpSum(pm_req[i] * x[i][t] for i in range(n))
                    <= period.max_person_months,
                    f"PersonaMes_{period.year}"
                )

        # ── R5: Concurrencia máxima por vigencia ──────────────────────────────
        for t, period in enumerate(periods):
            if period.max_projects_concurrent < 999:
                prob += (
                    pulp.lpSum(x[i][t] for i in range(n))
                    <= period.max_projects_concurrent,
                    f"Concurrencia_{period.year}"
                )

        # ── R6: Riesgo máximo acumulado del portafolio por vigencia ───────────
        # El riesgo de un proyecto = Riesgo_Base × (1 - reduccion_proyecto)
        # Aquí usamos CAPEX como proxy de riesgo de ejecución si no hay datos
        for t, period in enumerate(periods):
            if period.max_risk_COP < float('inf'):
                risk_expr = []
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
                    risk_expr.append(risk_proxy * x[i][t])
                if risk_expr:
                    prob += (
                        pulp.lpSum(risk_expr) <= period.max_risk_COP,
                        f"RiesgoPortafolio_{period.year}"
                    )

        # ── R9: Continuous Planning — fijar binarios para completados/en-curso ─
        # Referencia: RFI §5 fila 143 · Copperleaf "use actual spend in optimizer"
        # Completados: x[i][t] = 0 para todo t → no entran en decisiones futuras.
        # En curso: x[i][t_exec] = 1 (irrevocable), 0 para el resto de vigencias.
        for i in _completed_idxs:
            for t in range(T):
                prob += (x[i][t] == 0, f"R9_Completed_{i}_t{t}")

        for i in _in_progress_idxs:
            proj   = projects[i]
            t_exec = year_idx.get(proj.execution_year)
            if t_exec is not None:
                # Fijar ejecución al año real (irrevocable — no se puede des-ejecutar)
                prob += (x[i][t_exec] == 1, f"R9_InProgress_{i}_forced")
                for t in range(T):
                    if t != t_exec:
                        prob += (x[i][t] == 0, f"R9_InProgress_{i}_t{t}_off")
            else:
                # Año de ejecución fuera del horizonte de planificación actual
                # → tratar como completado (AC ya gastado, no entra en decisiones)
                for t in range(T):
                    prob += (x[i][t] == 0, f"R9_InProgress_out_{i}_t{t}")

        if _completed_idxs or _in_progress_idxs:
            print(f"  [R9] Continuous Planning: "
                  f"{len(_completed_idxs)} completados excluidos, "
                  f"{len(_in_progress_idxs)} en curso fijados con ETC.")

        # ── R10: Outage-Aligned Scheduling ( — C55 §5.4) ─────────────
        # Para proyectos con require_outage_alignment=True, el MILP solo permite
        # ejecutarlos en años que tengan ≥1 PlannedOutage con el mismo asset_id.
        # Cruce automático via asset_id (Opción 3 — sin outage_ids explícitos).
        _outage_calendar: Optional[OutageCalendar] = None
        _r10_active_idxs: Dict[int, List[int]] = {}    # i → valid_years aplicados
        _r10_warnings: List[str] = []                   # proyectos sin ventana disponible

        if planned_outages:
            _outage_calendar = OutageCalendar(planned_outages)
            for i, proj in enumerate(projects):
                if not getattr(proj, "require_outage_alignment", False):
                    continue
                if not proj.asset_id:
                    _r10_warnings.append(
                        f"[R10] '{proj.name}' require_outage_alignment=True "
                        f"pero asset_id vacío — sin restricción R10."
                    )
                    continue
                valid_yrs = _outage_calendar.valid_years(proj.asset_id)
                if not valid_yrs:
                    _r10_warnings.append(
                        f"[R10] '{proj.name}' (asset_id='{proj.asset_id}') no tiene "
                        f"outages en el calendario — sin restricción R10 aplicada."
                    )
                    continue
                # Bloquear todos los años sin ventana de outage
                valid_set = set(valid_yrs)
                _r10_active_idxs[i] = valid_yrs
                for t in range(T):
                    if years[t] not in valid_set:
                        prob += (x[i][t] == 0,
                                 f"R10_OutageAlign_{i}_t{t}")

        if _r10_active_idxs:
            print(f"  [R10] Outage-Aligned: {len(_r10_active_idxs)} proyectos "
                  f"restringidos a ventanas de mantenimiento.")
        for _w in _r10_warnings:
            print(f"  {_w}")

        # ── R11: Committed Spend ─────────────────────────────────
        # Proyectos con contrato firmado (is_committed=True) deben ejecutarse
        # en el año del horizonte más cercano a committed_year (o execution_year).
        # El presupuesto comprometido es irrevocable — no entra en la optimización
        # discrecional. El solver lo fija igual que un completed project pero
        # SIN eliminar del portafolio (a diferencia de R9 para completados).
        _r11_committed_idxs: Dict[int, int] = {}  # i → t_committed
        for i, proj in enumerate(projects):
            if not getattr(proj, "is_committed", False):
                continue
            if i in _completed_idxs or i in _in_progress_idxs:
                continue  # R9 ya lo maneja
            target_year = getattr(proj, "committed_year", 0) or proj.execution_year
            # Año del horizonte más cercano al año comprometido
            best_t = min(range(T), key=lambda t: abs(years[t] - target_year))
            _r11_committed_idxs[i] = best_t
            prob += (x[i][best_t] == 1, f"R11_Committed_{i}_t{best_t}")
            for t in range(T):
                if t != best_t:
                    prob += (x[i][t] == 0, f"R11_Committed_{i}_t{t}_off")

        if _r11_committed_idxs:
            print(f"  [R11] Committed Spend: {len(_r11_committed_idxs)} proyectos "
                  f"fijados por contrato/compromiso contractual.")

        # ── R12: TOTEX por vigencia ──────────────────────────────
        # TOTEX_i = CAPEX_i + NPV(OPEX_incremental_i × horizonte)
        # Factor NPV para OPEX: annuity factor WACC=10%, horizonte=10 años ≈ 6.14
        # Si totex_budget_COP está definido en ResourcePeriod, aplicar restricción.
        # Nota: los datos de OPEX histórico normalmente provienen de SIGEAS pero
        # en este modelo los ingresa el analista en opex_incremental_COP_year.
        _wacc_param    = float(self.config.get("wacc", 0.10))
        _horizon_param = int(self.config.get("horizon_years", 10))
        _npv_factor    = ((1 - (1 + _wacc_param) ** -_horizon_param) / _wacc_param
                          if _wacc_param > 0 else float(_horizon_param))
        _r12_active = any(
            getattr(p, "totex_budget_COP", None) is not None for p in periods)
        if _r12_active:
            for t, period in enumerate(periods):
                tb = getattr(period, "totex_budget_COP", None)
                if tb is None:
                    continue
                totex_expr = []
                for i, proj in enumerate(projects):
                    cy = max(1, proj.construction_years)
                    cap_by_year = getattr(proj.costs, 'capex_by_year', {}) or {}
                    declared = proj.declared_execution_year or proj.execution_year
                    # Mismo defecto que tenía R1 antes de corregirse: buscaba
                    # period.year literal en vez de reanclar al candidato t.
                    # R12 solo carga TOTEX en la vigencia de inicio (s=t, ver
                    # comentario de abajo), así que — igual que capex_matrix
                    # arriba — solo puede representar un valor por candidato,
                    # no un ledger multi-año: se usa el tramo declarado para
                    # el año de arranque (offset 0).
                    if cap_by_year:
                        # Mismo patrón que capex_matrix: se usa el total del
                        # perfil, no solo el tramo declarado — R12 solo
                        # carga TOTEX en la vigencia de inicio (s=t), así
                        # que atribuye el costo completo del proyecto a ese
                        # único candidato, igual que capex_matrix.
                        capex_this = sum(cap_by_year.values())
                    else:
                        capex_this = proj.costs.capex_COP / cy
                    opex_npv = (getattr(proj.costs, "opex_incremental_COP_year", 0.0)
                                * _npv_factor)
                    totex_i = capex_this + opex_npv
                    if totex_i > 0:
                        # Sólo suma TOTEX en la vigencia de inicio (s=t)
                        totex_expr.append(totex_i * x[i][t])
                if totex_expr:
                    prob += (pulp.lpSum(totex_expr) <= tb,
                             f"R12_TOTEX_{period.year}")
            print(f"  [R12] TOTEX Constraint activa en "
                  f"{sum(1 for p in periods if getattr(p,'totex_budget_COP',None) is not None)} "
                  f"vigencias (Factor NPV OPEX={_npv_factor:.2f}).")

        # ── Bono de timing por DegradationEngine ──────────────────
        # Para proyectos con asset_id en degradation_hints, se agrega un pequeño
        # bono positivo (+5% VNT) en el año sugerido por el motor de degradación.
        # Esto implementa una preferencia SUAVE — el solver puede ignorarla si
        # otra vigencia es economicamente superior. No es hard constraint.
        _hint_bonus_pct = 0.05   # 5% de VNT base como bono de timing
        _hints_applied: List[dict] = []
        if degradation_hints:
            for i, proj in enumerate(projects):
                aid = getattr(proj, "asset_id", "") or ""
                if not aid or aid not in degradation_hints:
                    continue
                hint_yr = degradation_hints[aid]
                # Buscar el año del horizonte más cercano al hint
                best_t_hint = min(range(T), key=lambda t: abs(years[t] - hint_yr))
                bonus = abs(vnt[i][best_t_hint]) * _hint_bonus_pct
                if bonus > 0:
                    # Ajustar coeficiente VNT en la función objetivo para ese año
                    # NOTA: esto modifica el coeficiente ya en la función objetivo;
                    # en PuLP la FO ya está definida — usamos una variable auxiliar
                    # de penalización en forma de constraint de bonificación implícita.
                    # Implementación práctica: ajustar vnt_cascade en ese slot antes
                    # de que se construya la FO (ya fue construida), entonces usamos
                    # un workaround: agregar una expresión de bono a la FO directamente.
                    prob.objective += bonus * x[i][best_t_hint]
                    _hints_applied.append({
                        "project_id": proj.id,
                        "asset_id":   aid,
                        "hint_year":  hint_yr,
                        "horizon_year": years[best_t_hint],
                        "bonus_COP":  bonus,
                        "bonus_pct":  _hint_bonus_pct * 100,
                    })

        if _hints_applied:
            print(f"  [I2] DegradationHints: bono de timing aplicado a "
                  f"{len(_hints_applied)} proyectos.")

        # ── Resolver ──────────────────────────────────────────────────────────
        solver = pulp.PULP_CBC_CMD(
            msg=0,
            timeLimit=solver_time_limit_s,
            gapRel=mip_gap,
        )
        status = prob.solve(solver)
        t1 = time.time()

        solve_time = t1 - t0
        solver_status = normalize_solver_status(pulp.LpStatus[prob.status])

        # ── Procesar resultado ────────────────────────────────────────────────
        selected, postponed = [], []
        total_nv = 0.0
        total_capex = 0.0
        assigned = set()

        if prob.status == 1:  # Optimal o feasible
            for i, proj in enumerate(projects):
                for t, period in enumerate(periods):
                    val = pulp.value(x[i][t])
                    if val is not None and val > 0.5:
                        nv = vnt[i][t]
                        # Total real: si hay perfil explícito, capex_COP es
                        # el escalar sin usar (0 típicamente) — reportar ese
                        # valor en vez del total del perfil deja "selected"
                        # con capex_COP=0 para un proyecto que el motor sí
                        # contabilizó bien internamente.
                        _cap_by_year_rep = getattr(proj.costs, 'capex_by_year', {}) or {}
                        cap_total = sum(_cap_by_year_rep.values()) if _cap_by_year_rep else proj.costs.capex_COP
                        # Info de precedencia para el reporte
                        prec_id = getattr(proj, "precedent_project_id", "")
                        lag_yr  = getattr(proj, "min_lag_years", 0)
                        excl_ids = getattr(proj, "mutual_exclusion_ids", []) or []
                        # Detectar si el proyecto fue "arrastrado" por su padre
                        dragged = False
                        drag_reason = ""
                        if prec_id:
                            # Buscar el año de ejecución del padre
                            for sel_item in selected:
                                if sel_item["project_id"] == prec_id:
                                    padre_year = sel_item["year"]
                                    earliest_possible = padre_year + lag_yr
                                    if period.year > proj.optimal_year:
                                        dragged = True
                                        drag_reason = (
                                            f"Postergado de {proj.optimal_year} a "
                                            f"{period.year} por precedencia con "
                                            f"'{prec_id}' (lag={lag_yr}a)"
                                        )
                                    break
                        selected.append({
                            "project_id":   proj.id,
                            "project_name": proj.name,
                            "business_unit":proj.business_unit,
                            "year":         period.year,
                            "net_value_COP":nv,
                            "capex_COP":    cap_total,
                            "vei":          nv / cap_total if cap_total > 0 else 0,
                            "person_months":pm_req[i],
                            "construction_years": proj.construction_years,
                            "is_mandatory": proj.is_mandatory,
                            # ── Info de precedencia ──────────────────────────
                            "precedent_project_id": prec_id or None,
                            "min_lag_years": lag_yr,
                            "mutual_exclusion_ids": excl_ids,
                            "dragged_by_precedence": dragged,
                            "drag_reason": drag_reason,
                            # ── Metadata ───────────────────────────
                            "asset_id": getattr(proj, "asset_id", ""),
                            "exclusion_group_id": getattr(proj, "exclusion_group_id", None),
                        })
                        total_nv += nv
                        total_capex += cap_total
                        assigned.add(proj.id)

            for proj in projects:
                if proj.id not in assigned:
                    postponed.append({
                        "project_id":   proj.id,
                        "project_name": proj.name,
                        "reason":       "Sin cupo (presupuesto, personal o riesgo)",
                        "exclusion_group_id": getattr(proj, "exclusion_group_id", None),
                        "asset_id": getattr(proj, "asset_id", ""),
                    })

        selected.sort(key=lambda r: r["net_value_COP"], reverse=True)

        # Las descartadas por EAV se conocen ANTES de correr el solver — se
        # reportan sin importar si este tuvo éxito o no (spec sección 6:
        # "no desaparecen en silencio, se reportan con la razón").
        for descartada, ganadora in _preselect_descartados:
            postponed.append({
                "project_id": descartada.id,
                "project_name": descartada.name,
                "reason": f"Descartada por EAV frente a {ganadora.id}",
                "exclusion_group_id": getattr(descartada, "exclusion_group_id", None),
                "asset_id": getattr(descartada, "asset_id", ""),
            })

        # ── Utilización por vigencia ──────────────────────────────────────────
        budget_util, resource_util, risk_util = {}, {}, {}
        if prob.status == 1:
            for t, period in enumerate(periods):
                used_cap = sum(
                    capex_matrix[i][t]
                    for i in range(n)
                    for s in range(T)
                    if pulp.value(x[i][s]) is not None and pulp.value(x[i][s]) > 0.5 and s == t
                )
                used_pm = sum(
                    pm_req[i]
                    for i in range(n)
                    if any(
                        pulp.value(x[i][t2]) is not None and pulp.value(x[i][t2]) > 0.5
                        for t2 in [t]
                    )
                )
                budget_util[period.year] = {
                    "used_COP":      used_cap,
                    "available_COP": period.budget_COP,
                    "pct":           used_cap / period.budget_COP * 100 if period.budget_COP > 0 else 0,
                }
                resource_util[period.year] = {
                    "used_pm":     used_pm,
                    "available_pm":period.max_person_months,
                    "pct":         used_pm / period.max_person_months * 100
                                   if period.max_person_months < float('inf') else 0,
                }
                risk_util[period.year] = {
                    "max_COP": period.max_risk_COP if period.max_risk_COP < float('inf') else None,
                }

        # ── Frontera eficiente ────────────────────────────────────────────────
        # Se calcula ANTES del precio sombra porque es su fuente correcta.
        frontier = []
        if compute_frontier:
            frontier = self._compute_frontier(
                projects, periods,
                n_points=frontier_n_points,
                max_pct=frontier_max_pct,
            )

        # ── Precio sombra del presupuesto ─────────────────────────────────────
        # En un MILP la dualidad LP NO se sostiene: con variables enteras el
        # dual del solver (constraint.pi) corresponde a la relajación lineal del
        # nodo final, no al valor marginal del problema entero. Publicarlo como
        # precio sombra produce una cifra que parece rigurosa y no lo es.
        #
        # El valor marginal correcto se obtiene por diferencia finita sobre la
        # frontera: cuántos pesos de VNT compra el siguiente peso de cupo. Es
        # escalonado en vez de continuo, que es exactamente lo que corresponde a
        # un problema entero.
        shadow_prices: Dict[str, float] = {}

        if compute_shadow_prices:
            _at_baseline = next(
                (p for p in frontier
                 if p.get("is_baseline") and p.get("marginal_value") is not None),
                None,
            )
            if _at_baseline is not None:
                shadow_prices["Presupuesto_marginal"] = _at_baseline["marginal_value"]

            # Duales de la relajación LP para las demás restricciones.
            # Se conservan como REFERENCIA de qué restricciones están activas,
            # con prefijo explícito para que no se lean como valor marginal del
            # problema entero. No son precios sombra del MILP.
            if prob.status == 1:
                _lp_prefixes = (
                    "PersonaMes_", "Concurrencia_", "RiesgoPortafolio_",
                    "PisoInversion_", "ExclGroup_",
                    "Dep_", "Prec_", "Mutex_",
                    "MustMitigate_", "R9_", "R10_",
                )
                for name, constraint in prob.constraints.items():
                    if name.startswith(_lp_prefixes):
                        pi = constraint.pi
                        if pi is not None and abs(pi) > 1:
                            shadow_prices[f"relajLP::{name}"] = pi

        # ── Reporte de proyectos completados y resumen actuals ───────
        completed_list: List[dict] = []
        _actuals_summary: dict = {}
        if _actual_map:
            # Proyectos completados — fuera del MILP pero visibles en el reporte
            for i in _completed_idxs:
                proj = projects[i]
                act  = _actual_map.get(proj.id)
                evm  = _evm_by_idx.get(i)
                entry: dict = {
                    "project_id":    proj.id,
                    "project_name":  proj.name,
                    "business_unit": proj.business_unit,
                    "status":        "COMPLETADO",
                    "ac_COP":        act.ac_COP if act else 0.0,
                    "capex_plan_COP":proj.costs.capex_COP,
                    "pct_complete":  1.0,
                }
                if evm:
                    entry.update({"cpi": evm.cpi, "spi": evm.spi,
                                  "cv_COP": evm.cv_COP, "sv_COP": evm.sv_COP,
                                  "eac_COP": evm.eac_COP, "health": evm.health})
                completed_list.append(entry)

            # Enriquecer proyectos en-curso en `selected` con métricas EVM
            for item in selected:
                pid = item.get("project_id", "")
                # Buscar índice
                idx = next((i for i, p in enumerate(projects) if p.id == pid), None)
                if idx is not None and idx in _in_progress_idxs:
                    evm = _evm_by_idx.get(idx)
                    if evm:
                        item["status"]        = "EN_CURSO"
                        item["pct_complete"]  = evm.pct_complete
                        item["ac_COP"]        = evm.ac_COP
                        item["etc_COP"]       = evm.etc_COP
                        item["eac_COP"]       = evm.eac_COP
                        item["spi"]           = evm.spi
                        item["cpi"]           = evm.cpi
                        item["health"]        = evm.health

            # Resumen agregado de actuals
            _all_evm = list(_evm_by_idx.values())
            if _all_evm:
                _pv_t  = sum(r.pv_COP  for r in _all_evm)
                _ac_t  = sum(r.ac_COP  for r in _all_evm)
                _ev_t  = sum(r.ev_COP  for r in _all_evm)
                _eac_t = sum(r.eac_COP for r in _all_evm)
                _n_planned = len(_actual_map) - len(_completed_idxs) - len(_in_progress_idxs)
                _capex_freed = sum(
                    projects[i].costs.capex_COP for i in _completed_idxs
                )
                _actuals_summary = {
                    "n_total":              len(_actual_map),
                    "n_completed":          len(_completed_idxs),
                    "n_in_progress":        len(_in_progress_idxs),
                    "n_planned":            max(0, _n_planned),
                    "spi_portfolio":        _ev_t / _pv_t if _pv_t > 0 else 1.0,
                    "cpi_portfolio":        _ev_t / _ac_t if _ac_t > 0 else 1.0,
                    "budget_consumed_COP":  _ac_t,
                    "eac_total_COP":        _eac_t,
                    "capex_freed_COP":      _capex_freed,
                    "budget_variance_COP":  _eac_t - sum(
                        p.costs.capex_COP for i, p in enumerate(projects)
                        if i in _completed_idxs or i in _in_progress_idxs),
                }

        # ── Construir outage_schedule ───────────────────────────────
        _outage_schedule: List[dict] = []
        if planned_outages:
            # Índice outage_id → PlannedOutage para lookup
            _o_by_id = {o.outage_id: o for o in planned_outages}
            # Índice asset_id → lista de PlannedOutage ordenados por año
            _o_by_asset: Dict[str, List[PlannedOutage]] = {}
            for _o in planned_outages:
                _o_by_asset.setdefault(_o.asset_id, []).append(_o)

            for i, proj in enumerate(projects):
                if not getattr(proj, "require_outage_alignment", False):
                    continue
                # Año que el MILP asignó a este proyecto
                _yr_sel = None
                for _item in selected:
                    if _item.get("id") == proj.id:
                        _yr_sel = _item.get("year")
                        break

                _valid_yrs = _r10_active_idxs.get(i, [])
                _warning = ""
                if not _valid_yrs:
                    _warning = (f"Sin outage en calendario para asset_id='{proj.asset_id}'")

                # Qué outage coincide con el año seleccionado
                _matching_outages = [
                    _o for _o in _o_by_asset.get(proj.asset_id, [])
                    if _o.year == _yr_sel
                ]
                _o_match = _matching_outages[0] if _matching_outages else None

                _outage_schedule.append({
                    "project_id":    proj.id,
                    "project_name":  proj.name,
                    "asset_id":      proj.asset_id,
                    "year_selected": _yr_sel,
                    "outage_id":     _o_match.outage_id if _o_match else "",
                    "outage_name":   _o_match.name      if _o_match else "",
                    "outage_type":   _o_match.outage_type if _o_match else "",
                    "valid_years":   _valid_yrs,
                    "r10_active":    i in _r10_active_idxs,
                    "warning":       _warning,
                })

        # ── Committed Summary ─────────────────────────────────────
        _committed_summary: dict = {}
        _committed_projects_list = []
        _capex_committed = 0.0
        _capex_discretionary = total_capex
        for i, proj in enumerate(projects):
            if not getattr(proj, "is_committed", False):
                continue
            # Si está en el portafolio final
            for sel_item in selected:
                if sel_item["project_id"] == proj.id:
                    _capex_committed += proj.costs.capex_COP
                    _committed_projects_list.append({
                        "project_id": proj.id,
                        "project_name": proj.name,
                        "business_unit": proj.business_unit,
                        "year": sel_item.get("year"),
                        "capex_COP": proj.costs.capex_COP,
                        "committed_year": getattr(proj, "committed_year", 0),
                    })
                    break
        _capex_discretionary = total_capex - _capex_committed
        _committed_summary = {
            "n_committed":            len(_committed_projects_list),
            "capex_committed_COP":    _capex_committed,
            "capex_discretionary_COP": _capex_discretionary,
            "pct_committed":          (_capex_committed / total_capex * 100
                                       if total_capex > 0 else 0.0),
            "committed_projects":     _committed_projects_list,
        }

        # ── TOTEX utilization por vigencia ─────────────────────────
        _totex_util: Dict[int, dict] = {}
        if prob.status == 1:
            for t, period in enumerate(periods):
                _cap_t = 0.0
                _opex_npv_t = 0.0
                for i in range(n):
                    val = pulp.value(x[i][t])
                    if val is not None and val > 0.5:
                        _cap_t += capex_matrix[i][t]
                        _opex_npv_t += (
                            getattr(projects[i].costs, "opex_incremental_COP_year", 0.0)
                            * _npv_factor
                        )
                tb = getattr(period, "totex_budget_COP", None)
                _totex_util[period.year] = {
                    "capex_COP":   _cap_t,
                    "opex_npv_COP": _opex_npv_t,
                    "totex_COP":   _cap_t + _opex_npv_t,
                    "budget_COP":  tb,
                    "pct":         ((_cap_t + _opex_npv_t) / tb * 100
                                    if tb and tb > 0 else None),
                }

        # ── Frontera eficiente TOTEX (opcional) ───────────────────
        _totex_frontier: List[dict] = []
        if compute_totex_frontier:
            _totex_frontier = self._compute_totex_frontier(projects, periods)

        # Gap
        obj_val = pulp.value(prob.objective) or 0.0
        gap = abs(prob.sol_status) if hasattr(prob, 'sol_status') else 0.0

        return EnterprisePortfolioResult(
            selected=selected,
            postponed=postponed,
            infeasible=[],
            total_net_value=total_nv,
            total_capex=total_capex,
            solver_status=solver_status,
            solver_name="PuLP + CBC",
            objective_value=obj_val,
            gap_pct=gap,
            solve_time_s=solve_time,
            config_version=self._meta.get("version", "4.3"),
            budget_utilization=budget_util,
            resource_utilization=resource_util,
            risk_utilization=risk_util,
            shadow_prices=shadow_prices,
            efficient_frontier=frontier,
            completed=completed_list,
            actuals_summary=_actuals_summary,
            outage_schedule=_outage_schedule,
            # ── Gasto comprometido ─────────────────────────────────
            committed_summary=_committed_summary,
            # ── TOTEX: utilización, frontera y degradación ─────────
            totex_utilization=_totex_util,
            totex_frontier=_totex_frontier,
            degradation_hints_applied=_hints_applied,
        )

    def _compute_frontier(
        self,
        projects: List[ProjectInput],
        base_periods: List[ResourcePeriod],
        n_points: int = 10,
        max_pct: float = 1.5,
    ) -> List[dict]:
        """Frontera eficiente VNT vs. cupo de inversión.

        Tratamiento de la incertidumbre presupuestal
        ───────────────────────────────────────────
        A nivel de portafolio la incertidumbre dominante no es cuánto cuesta
        cada proyecto —errores independientes sobre muchos proyectos se
        compensan parcialmente— sino CUÁNTO CUPO va a haber realmente. Esa
        incertidumbre no se diversifica y nadie la controla.

        La frontera es el instrumento para tratarla: en lugar de suponer un
        presupuesto y entregar un plan, entrega el plan óptimo para CADA
        presupuesto posible y el valor marginal de moverse entre ellos.

        El barrido va de 10% a `max_pct` del cupo declarado. Pasar del 100% es
        lo que permite responder "¿cuánto presupuesto pedir?" y no solo
        "¿qué pierdo si me recortan?".

        Cada punto reporta el valor marginal del tramo — cuántos pesos de VNT
        compra el siguiente peso de cupo. Es también el precio sombra correcto
        del presupuesto: en un MILP la dualidad LP no aplica, así que el valor
        marginal se obtiene por diferencia finita, no del dual del solver.

        Args:
            projects:     Portafolio a evaluar.
            base_periods: Períodos con el cupo declarado (= 100%).
            n_points:     Puntos hasta el 100%. El tramo por encima usa el
                          mismo paso, así que el total es n_points × max_pct.
            max_pct:      Fracción máxima del cupo a explorar (1.5 = +50%).

        Returns:
            Lista de puntos ordenados por cupo. Cada punto incluye
            marginal_value (VNT ganado por peso adicional de cupo) y is_knee
            (punto de quiebre: donde más cae el valor marginal).
        """
        step = 1.0 / n_points
        pcts, _p = [], step
        while _p <= max_pct + 1e-9:
            pcts.append(round(_p, 6))
            _p += step

        frontier: List[dict] = []
        for pct in pcts:
            scaled = [
                ResourcePeriod(
                    year=p.year,
                    budget_COP=p.budget_COP * pct,
                    max_person_months=p.max_person_months,
                    max_projects_concurrent=p.max_projects_concurrent,
                    max_risk_COP=p.max_risk_COP,
                    totex_budget_COP=(
                        p.totex_budget_COP * pct
                        if getattr(p, "totex_budget_COP", None) else
                        getattr(p, "totex_budget_COP", None)
                    ),
                )
                for p in base_periods
            ]
            try:
                r = self.optimize(
                    projects, scaled,
                    compute_shadow_prices=False,
                    compute_frontier=False,
                )
                frontier.append({
                    "budget_pct":     pct,
                    "budget_COP":     sum(p.budget_COP for p in scaled),
                    "vnt_COP":        r.total_net_value,
                    "capex_COP":      r.total_capex,
                    "n_projects":     len(r.selected),
                    "solver_status":  r.solver_status,
                    "is_baseline":    abs(pct - 1.0) < 1e-9,
                })
            except Exception as e:
                frontier.append({"budget_pct": pct, "error": str(e)})

        self._annotate_frontier(frontier)
        return frontier

    @staticmethod
    def _annotate_frontier(frontier: List[dict]) -> None:
        """Añade valor marginal por tramo y marca el punto de quiebre.

        marginal_value = ΔVNT / Δcupo entre puntos consecutivos. Es el precio
        sombra del presupuesto calculado correctamente para un problema entero:
        cuántos pesos de valor compra el siguiente peso de cupo. Es escalonado,
        no continuo — un MILP no tiene derivada.

        El punto de quiebre (is_knee) es donde el valor marginal cae más
        bruscamente: a partir de ahí cada peso adicional compra mucho menos.
        Es la cifra de presupuesto que se puede defender técnicamente.
        """
        valid = [p for p in frontier if "error" not in p]
        prev = None
        for p in valid:
            if prev is None or (p["budget_COP"] - prev["budget_COP"]) <= 0:
                p["marginal_value"] = None
            else:
                p["marginal_value"] = (
                    (p["vnt_COP"] - prev["vnt_COP"])
                    / (p["budget_COP"] - prev["budget_COP"])
                )
            prev = p

        # ── Punto de quiebre — método de la cuerda ───────────────────────────
        # No se usa "la mayor caída del valor marginal" porque en un MILP la
        # curva es escalonada y lumpy: la entrada de un proyecto grande produce
        # saltos que no representan un cambio de régimen. El método de la cuerda
        # busca el punto más alejado de la recta que une los extremos, que es
        # donde la curva realmente deja de crecer al ritmo inicial.
        for p in valid:
            p["is_knee"] = False
        if len(valid) < 3:
            return

        x0, y0 = valid[0]["budget_COP"],  valid[0]["vnt_COP"]
        x1, y1 = valid[-1]["budget_COP"], valid[-1]["vnt_COP"]
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy)
        if norm <= 0:
            return

        best_d, best_i = 0.0, None
        for i, p in enumerate(valid[1:-1], start=1):
            # Distancia perpendicular del punto a la cuerda, con signo:
            # solo interesan los puntos POR ENCIMA de la recta (curva cóncava,
            # rendimientos decrecientes). Un punto por debajo indica lo contrario.
            cross = dx * (p["vnt_COP"] - y0) - dy * (p["budget_COP"] - x0)
            d = cross / norm
            if d > best_d:
                best_d, best_i = d, i
        if best_i is not None:
            valid[best_i]["is_knee"] = True

    def _compute_totex_frontier(
        self,
        projects: List[ProjectInput],
        base_periods: List[ResourcePeriod],
        n_points: int = 8,
    ) -> List[dict]:
        """Calcula la frontera eficiente VNT vs. techo TOTEX.

        Barre el techo TOTEX desde 50% hasta 150% del TOTEX baseline (suma
        CAPEX + NPV(OPEX) del portafolio completo sin restricción).
        Responde la pregunta: ¿cuánto VNT adicional se obtiene si se amplía
        el límite TOTEX? Análogo a efficient_frontier pero en eje TOTEX.

        El dato de OPEX ingresa manualmente por el analista (opex_incremental
        de cada ProjectInput) — no requiere conexión con SIGEAS.
        """
        wacc   = float(self.config.get("wacc", 0.10))
        horiz  = int(self.config.get("horizon_years", 10))
        npv_f  = ((1 - (1 + wacc) ** -horiz) / wacc if wacc > 0 else float(horiz))

        # TOTEX baseline: suma de (CAPEX + OPEX_NPV) de todos los proyectos
        baseline_totex = sum(
            p.costs.capex_COP
            + getattr(p.costs, "opex_incremental_COP_year", 0.0) * npv_f
            for p in projects
        )
        if baseline_totex <= 0:
            return []

        frontier = []
        for pct in [0.5 + i * (1.0 / n_points) for i in range(n_points + 1)]:
            tb = baseline_totex * pct
            scaled_periods = [
                ResourcePeriod(
                    year=p.year,
                    budget_COP=p.budget_COP,
                    max_person_months=p.max_person_months,
                    max_projects_concurrent=p.max_projects_concurrent,
                    max_risk_COP=p.max_risk_COP,
                    totex_budget_COP=tb / len(base_periods),  # distribuir uniformemente
                )
                for p in base_periods
            ]
            try:
                r = self.optimize(
                    projects, scaled_periods,
                    compute_shadow_prices=False,
                    compute_frontier=False,
                    compute_totex_frontier=False,
                )
                # TOTEX real del portafolio seleccionado
                sel_totex = sum(
                    p.costs.capex_COP
                    + getattr(p.costs, "opex_incremental_COP_year", 0.0) * npv_f
                    for p in projects if p.id in {s["project_id"] for s in r.selected}
                )
                frontier.append({
                    "totex_pct":     pct,
                    "totex_techo_COP": tb,
                    "totex_real_COP":  sel_totex,
                    "vnt_COP":       r.total_net_value,
                    "capex_COP":     r.total_capex,
                    "opex_npv_COP":  sel_totex - r.total_capex,
                    "n_projects":    len(r.selected),
                    "solver_status": r.solver_status,
                })
            except Exception as e:
                frontier.append({"totex_pct": pct, "error": str(e)})
        return frontier

    def print_report(self, result: EnterprisePortfolioResult,
                     periods: List[ResourcePeriod]) -> None:
        """Imprime reporte ejecutivo del resultado."""
        sep = "═" * 70
        print(f"\n{sep}")
        print(f"  PORTAFOLIO ENTERPRISE — {result.solver_name}")
        print(f"  Estado: {result.solver_status}  |  "
              f"Tiempo: {result.solve_time_s:.1f}s  |  "
              f"v{result.config_version}")
        print(sep)
        print(f"\n  VNT Total:    COP {result.total_net_value/1e9:,.2f}B")
        print(f"  CAPEX Total:  COP {result.total_capex/1e9:,.2f}B")
        print(f"  Proyectos:    {len(result.selected)} seleccionados  |  "
              f"{len(result.postponed)} postergados")

        print(f"\n  {'─'*68}")
        print(f"  UTILIZACIÓN POR VIGENCIA")
        print(f"  {'─'*68}")
        print(f"  {'Año':>6} {'Presupuesto usado':>20} {'% ppto':>8} "
              f"{'Pers-mes':>10} {'% RRHH':>8}")
        for yr, bu in sorted(result.budget_utilization.items()):
            ru = result.resource_utilization.get(yr, {})
            pm_pct = f"{ru.get('pct', 0):.1f}%" if ru.get('available_pm', float('inf')) < float('inf') else "n/a"
            print(f"  {yr:>6}  COP {bu['used_COP']/1e9:>12.2f}B   {bu['pct']:>6.1f}%"
                  f"    {ru.get('used_pm', 0):>6.0f}    {pm_pct:>6}")

        # ── Frontera eficiente — tratamiento de la incertidumbre presupuestal ──
        _fr = [p for p in (result.efficient_frontier or []) if "error" not in p]
        if _fr:
            print(f"\n  {'─'*68}")
            print(f"  FRONTERA EFICIENTE — VNT vs. cupo de inversión")
            print(f"  {'─'*68}")
            print(f"  {'% cupo':>8} {'Cupo':>14} {'VNT':>14} {'Proy':>6} "
                  f"{'Valor marginal':>16}")
            print(f"  {'─'*8} {'─'*14} {'─'*14} {'─'*6} {'─'*16}")
            for p in _fr:
                mv = p.get("marginal_value")
                mv_txt = f"{mv:>13.2f} ×" if mv is not None else "—".rjust(16)
                mark = ""
                if p.get("is_baseline"):
                    mark = "  ◄ cupo declarado"
                if p.get("is_knee"):
                    mark += "  ◄ QUIEBRE"
                print(f"  {p['budget_pct']*100:>7.0f}% "
                      f"{p['budget_COP']/1e9:>12.1f}B "
                      f"{p['vnt_COP']/1e9:>12.1f}B "
                      f"{p['n_projects']:>6} {mv_txt}{mark}")

            _knee = next((p for p in _fr if p.get("is_knee")), None)
            if _knee:
                print()
                for _ln in _wrap_note(
                    f"Punto de quiebre en {_knee['budget_pct']*100:.0f}% del cupo declarado "
                    f"(COP {_knee['budget_COP']/1e9:,.1f}B). Más allá de ahí cada peso "
                    f"adicional compra sustancialmente menos valor. Es la cifra de "
                    f"presupuesto defendible técnicamente.", 68):
                    print(f"  {_ln}")

        if result.shadow_prices:
            print(f"\n  {'─'*68}")
            print(f"  VALOR MARGINAL DEL PRESUPUESTO")
            print(f"  {'─'*68}")
            _mv = result.shadow_prices.get("Presupuesto_marginal")
            if _mv is not None:
                print(f"  En el cupo declarado: cada $1B adicional agrega "
                      f"COP {_mv:.2f}B en VNT")
                print("  Método: diferencia finita sobre la frontera "
                      "(un MILP no tiene dual válido).")
            _lp = {k: v for k, v in result.shadow_prices.items()
                   if k.startswith("relajLP::")}
            if _lp:
                print(f"\n  Restricciones activas (duales de la relajación LP — "
                      f"REFERENCIALES, no valores marginales del problema entero):")
                print(f"  {'Restricción':<38}  {'Dual LP':>14}  Interpretación")
                print(f"  {'─'*38}  {'─'*14}  {'─'*20}")
            for _raw, pi in sorted(_lp.items(),
                                   key=lambda x: abs(x[1]), reverse=True)[:8]:
                name = _raw.replace("relajLP::", "")
                # Interpretación en lenguaje de negocio según tipo de restricción
                if name.startswith("PersonaMes_"):
                    yr = name.replace("PersonaMes_","")
                    interp = f"1 persona-mes más en {yr} agrega COP {abs(pi)/1e6:.1f}M en VNT"
                elif name.startswith("Concurrencia_"):
                    yr = name.replace("Concurrencia_","")
                    interp = f"1 proyecto más simultáneo en {yr} agrega COP {abs(pi)/1e9:.2f}B"
                elif name.startswith("RiesgoPortafolio_"):
                    yr = name.replace("RiesgoPortafolio_","")
                    interp = f"Umbral de riesgo {yr}: relajar $1M libera COP {abs(pi)/1e6:.1f}M VNT"
                elif name.startswith("MustMitigate_"):
                    aid = name.replace("MustMitigate_","").replace("_","-")
                    interp = f"Costo de no mitigar activo '{aid}': COP {abs(pi)/1e9:.2f}B perdidos"
                elif name.startswith("PisoInversion_"):
                    bu = name.replace("PisoInversion_","")
                    interp = f"Bajar piso BU {bu} en $1B libera COP {abs(pi)/1e9:.2f}B VNT"
                elif name.startswith("ExclGroup_"):
                    gid = name.replace("ExclGroup_","")
                    interp = f"Grupo excl. '{gid}': permitir 1 más agrega COP {abs(pi)/1e9:.2f}B"
                else:
                    interp = f"Relajar en 1 unidad agrega COP {abs(pi)/1e6:.1f}M VNT"
                print(f"  {name:<38}  COP {abs(pi)/1e9:>8.3f}B  {interp}")

        print(f"\n  {'─'*68}")
        print(f"  TOP 10 PROYECTOS SELECCIONADOS")
        print(f"  {'─'*68}")
        dragged_count = sum(1 for r in result.selected if r.get("dragged_by_precedence"))
        mutex_count = sum(1 for r in result.selected if r.get("mutual_exclusion_ids"))
        prec_count  = sum(1 for r in result.selected if r.get("precedent_project_id"))

        for r in result.selected[:15]:
            mand  = "★" if r.get("is_mandatory") else " "
            drag  = "⛓" if r.get("dragged_by_precedence") else " "
            child = "↳" if r.get("precedent_project_id") else " "
            print(f"  {mand}{drag}{child} {r['year']}  {r['project_id']:<20} "
                  f"VNT {r['net_value_COP']/1e6:>8.0f}MM  "
                  f"VEI {r['vei']:.2f}×  "
                  f"{r['project_name'][:28]}")
            if r.get("drag_reason"):
                print(f"       ⚠  {r['drag_reason']}")

        if prec_count or dragged_count:
            print(f"\n  Leyenda: ★=obligatorio  ⛓=postergado por precedencia  ↳=hijo de otro proyecto")
            print(f"  Proyectos con precedencia: {prec_count}  |  Postergados: {dragged_count}")

        # ── Resumen: Grupos de exclusión mutua resueltos ─────────────
        from collections import defaultdict as _ddc
        excl_grps_selected: dict = _ddc(list)
        for r in result.selected:
            gid = r.get("exclusion_group_id")
            if gid:
                excl_grps_selected[gid].append(r["project_id"])
        excl_grps_rejected: dict = _ddc(list)
        for r in result.postponed:
            gid = r.get("exclusion_group_id")
            if gid and gid in excl_grps_selected:
                excl_grps_rejected[gid].append(r["project_id"])

        if excl_grps_selected:
            print(f"\n  {'─'*68}")
            print(f"  GRUPOS DE EXCLUSIÓN MUTUA RESUELTOS")
            print(f"  {'─'*68}")
            for gid, winners in excl_grps_selected.items():
                losers = excl_grps_rejected.get(gid, [])
                print(f"  Grupo '{gid}':")
                print(f"    ✓ Seleccionado:  {winners}")
                if losers:
                    print(f"    ✗ Descartados:   {losers}")

        # ── Resumen: Activos con riesgo inaceptable forzados ──────────
        must_mitig = [k for k in result.shadow_prices.keys()
                      if k.startswith("MustMitigate_")]
        if must_mitig:
            print(f"\n  {'─'*68}")
            print(f"  RESTRICCIONES MUST-MITIGATE ACTIVAS")
            print(f"  {'─'*68}")
            for mm in must_mitig:
                aid = mm.replace("MustMitigate_","").replace("_","-")
                pi  = result.shadow_prices.get(mm, 0)
                print(f"  Activo '{aid}': riesgo inaceptable forzado → "
                      f"shadow price COP {abs(pi)/1e9:.3f}B")

        print(f"\n{sep}\n")

    # ── Robustez del portafolio ante la incertidumbre de estimación ──────────
    def estimate_robustness(
        self,
        projects: List["ProjectInput"],
        periods: List["ResourcePeriod"],
        top_n: int = 10,
        **optimize_kwargs,
    ) -> "SensitivityResult":
        """Robustez del portafolio ante el error de estimación de cada CAPEX.

        Cada proyecto se mueve dentro de SU PROPIA banda, la que declara su
        CostInput.estimate_class. Un proyecto con oferta en firme se mueve ±5-10%
        y uno en analogía se mueve -30/+50%. Esa diferencia es exactamente la
        señal que interesa: quién entra al portafolio por mérito y quién por
        tener un estimativo que nadie ha apretado.

        Aplicar una clase única a todo el portafolio —como hacía la versión
        anterior— hacía que la estabilidad reportada dependiera de la posición
        del proyecto respecto al corte presupuestal y no de la calidad de su
        dato, que es lo contrario de lo que el indicador pretende comunicar.

        NO escala beneficios ni riesgo. La incertidumbre de condición del activo
        ya la trata el Weibull en _calc_risk; moverla aquí sería doble conteo, y
        moverla en sentido contrario al costo produce un "mundo" incoherente
        (costos altos con activo sano) que castiga sistemáticamente la reposición.

        Args:
            projects: Portafolio a evaluar.
            periods:  Períodos presupuestales.
            top_n:    Tamaño del top-N reportado (no afecta la clasificación).

        Returns:
            SensitivityResult con los 3 puntos y la robustez de TODOS los
            proyectos evaluados, no solo los del top-N.
        """
        bands_cfg = self.config.get("cost_estimate_uncertainty", {})
        default_cls = bands_cfg.get("default_class", "Class5")

        # Los 3 puntos no son "mundos": son los extremos del error de estimación.
        # "Costos altos" = cada proyecto en el extremo superior de SU banda.
        points = [("Costos altos", "high"), ("Base", None), ("Costos bajos", "low")]

        kw = {k: v for k, v in optimize_kwargs.items() if k != "compute_frontier"}
        kw["compute_shadow_prices"] = False
        kw["compute_frontier"] = False

        scenarios: List[SensitivityScenario] = []
        rank_by_point: Dict[str, Dict[str, int]] = {}

        for label, side in points:
            scaled = [_scale_project_for_estimate(p, side, self.config)
                      for p in projects]
            r = self.optimize(scaled, periods, **kw)
            ids = [s["project_id"] for s in r.selected]
            rank_by_point[label] = {pid: i + 1 for i, pid in enumerate(ids)}
            # capex_factor/vnt_factor quedan en 1.0: el factor ya no es global,
            # cada proyecto usó el suyo. Se conservan por compatibilidad de UI.
            scenarios.append(SensitivityScenario(
                label=label,
                capex_factor=1.0,
                vnt_factor=1.0,
                selected_ids=ids,
                vnt_total_COP=r.total_net_value,
                capex_total_COP=r.total_capex,
                n_projects=len(r.selected),
            ))

        # ── Robustez: se clasifican TODOS los proyectos evaluados ─────────────
        # La versión anterior solo clasificaba los que aparecían en algún top-N,
        # así que un proyecto que entraba únicamente en el punto optimista
        # desaparecía del reporte sin dejar rastro.
        n_total = len(projects)
        stability: Dict[str, str] = {}
        for p in projects:
            pid = p.id
            hits = sum(1 for lbl in rank_by_point if pid in rank_by_point[lbl])
            if hits == 0:
                stability[pid] = "NUNCA_SELECCIONADO"
                continue
            if hits < 3:
                stability[pid] = "SENSIBLE" if hits == 2 else "INESTABLE"
                continue
            # Seleccionado en los 3 puntos → se mide CUÁNTO se movió de posición,
            # no si cruzó un umbral arbitrario.
            ranks = [rank_by_point[lbl][pid] for lbl in rank_by_point]
            spread = max(ranks) - min(ranks)
            rel = spread / max(n_total, 1)
            stability[pid] = ("ESTABLE" if rel <= 0.10
                              else "SENSIBLE" if rel <= 0.25
                              else "INESTABLE")

        base_vnt = scenarios[1].vnt_total_COP
        vnt_range_pct = (
            (scenarios[2].vnt_total_COP - scenarios[0].vnt_total_COP)
            / max(abs(base_vnt), 1)
        ) * 100.0

        # Composición de clases declaradas — hace visible de dónde viene el ancho
        _cls_mix: Dict[str, int] = {}
        for p in projects:
            k = getattr(p.costs, "estimate_class", None) or default_cls
            _cls_mix[k] = _cls_mix.get(k, 0) + 1

        return SensitivityResult(
            scenarios=scenarios,
            stability=stability,
            aace_class="por_alternativa",
            aace_label="Banda propia de cada alternativa (CostInput.estimate_class)",
            top_n=top_n,
            vnt_range_pct=vnt_range_pct,
            class_mix=_cls_mix,
            rank_by_point=rank_by_point,
            calibrated_with_epm_data=bool(bands_cfg.get("_calibrado_con_datos_epm", False)),
        )

    # Alias de compatibilidad — el nombre anterior sugería un análisis de
    # escenarios de mundo, que no es lo que hace.
    def sensitivity_analysis(self, projects, periods, aace_class=None,
                             top_n: int = 10, **kw) -> "SensitivityResult":
        """Obsoleto: usar estimate_robustness(). `aace_class` se ignora —
        la clase es ahora un atributo de cada alternativa, no de la corrida."""
        return self.estimate_robustness(projects, periods, top_n=top_n, **kw)


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN B3b: ROBUSTEZ ANTE LA INCERTIDUMBRE DE ESTIMACIÓN DEL CAPEX
# ══════════════════════════════════════════════════════════════════════════════
# Trata UNA incertidumbre: qué tan bien se conoce el costo de cada alternativa.
# Las bandas viven en DEFAULT_CONFIG["cost_estimate_uncertainty"] y se aplican
# POR PROYECTO según su CostInput.estimate_class.
#
# Qué NO hace este módulo, y por qué:
#   · No escala beneficios. El ±15% que había antes era una constante sin fuente
#     verificable. Entre alternativas de una misma necesidad la incertidumbre de
#     beneficios se cancela en buena parte (comparten drivers, precios y
#     horizonte), y a nivel de portafolio es sistemática pero no accionable:
#     no se reduce mejorando ningún dato. Era ruido con apariencia de rigor.
#   · No escala el riesgo. La incertidumbre de condición del activo ya la trata
#     el Weibull PoF(t) en _calc_risk. Moverla aquí sería doble conteo; y moverla
#     en sentido contrario al costo —como hacía la versión anterior, que bajaba
#     el riesgo baseline en el punto pesimista— construye un mundo incoherente
#     donde el activo está MÁS sano justo cuando todo va peor, y castiga
#     sistemáticamente el caso de reposición de activos envejecidos.
#   · No usa los rótulos "Pesimista"/"Optimista". No son estados del mundo, son
#     los extremos del error de estimación del costo.
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class SensitivityScenario:
    """Portafolio óptimo en uno de los extremos del error de estimación."""
    label: str                # "Costos altos" | "Base" | "Costos bajos"
    capex_factor: float       # 1.0 — el factor es por proyecto, no global
    vnt_factor: float         # 1.0 — no se escalan beneficios
    selected_ids: List[str]
    vnt_total_COP: float
    capex_total_COP: float
    n_projects: int


@dataclass
class SensitivityResult:
    """Robustez del portafolio ante el error de estimación del CAPEX."""
    scenarios: List[SensitivityScenario]   # [Costos altos, Base, Costos bajos]
    stability: Dict[str, str]
    """project_id → ESTABLE | SENSIBLE | INESTABLE | NUNCA_SELECCIONADO.
    Se clasifican TODOS los proyectos evaluados, no solo los del top-N."""
    aace_class: str
    aace_label: str
    top_n: int
    vnt_range_pct: float
    class_mix: Dict[str, int] = field(default_factory=dict)
    """Cuántos proyectos declaran cada clase. Hace visible de dónde viene el
    ancho del análisis: un portafolio mayoritariamente Class5 produce bandas
    amplias porque nadie ha apretado los estimativos, no porque el mundo sea
    incierto."""
    rank_by_point: Dict[str, Dict[str, int]] = field(default_factory=dict)
    """{punto: {project_id: posición}} — permite ver el movimiento real de
    posición, no solo si cruzó el corte."""
    calibrated_with_epm_data: bool = False
    """False mientras las bandas sean las de referencia de industria. Pasa a
    True cuando se reemplacen por las observadas en el histórico de EPM."""


def _scale_project_for_estimate(
    project: "ProjectInput",
    side: Optional[str],
    config: dict,
) -> "ProjectInput":
    """Copia del proyecto con el CAPEX en un extremo de SU banda de estimación.

    Args:
        project: Alternativa a escalar.
        side:    "low" (costo optimista), "high" (costo pesimista) o None (base).
        config:  Configuración con cost_estimate_uncertainty.

    Solo se escala el CAPEX. El OPEX queda intacto: las bandas AACE clasifican
    estimativos de costo de CAPITAL, no de costo operativo recurrente, y aplicar
    el mismo porcentaje a un flujo que se repite todo el horizonte sobrestima su
    efecto en el VNT frente al del CAPEX, que es un desembolso acotado.
    """
    if side is None:
        return project

    cfg   = config.get("cost_estimate_uncertainty", {})
    by_bu = cfg.get("classes_by_bu", {}) or {}
    bands = by_bu.get(project.business_unit) or cfg.get("classes", {})

    declared = getattr(project.costs, "estimate_class", None)
    cls_key  = declared if declared in bands else cfg.get("default_class", "Class5")
    band     = bands.get(cls_key, {})
    factor   = 1.0 + float(band.get(side, 0.0))

    p = copy.deepcopy(project)
    p.costs.capex_COP *= factor
    if p.costs.capex_by_year:
        p.costs.capex_by_year = {
            yr: v * factor for yr, v in p.costs.capex_by_year.items()
        }
    return p


# ══════════════════════════════════════════════════════════════════════════════
# VERSIONADO DE PLAN PAI (Plan Versioning)
# Referencia: Copperleaf C55 §5 "Approve & Execute" — historial de planes
# El analista guarda snapshots del plan aprobado por vigencia PAI.
# No requiere integración EAM/ERP — los datos son el output del optimizador.
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PlanVersion:
    """Snapshot de un plan de inversiones aprobado en un ciclo PAI.

    Captura el resultado del optimizador Enterprise en un momento dado,
    incluyendo metadatos del ciclo presupuestal y el autor del plan.
    Análogo al "Plan Aprobado" del diagrama de Copperleaf C55 §5.

    Ingresado por el analista al momento de aprobar el plan — no integrado
    con sistemas externos. La firma SHA-256 garantiza inmutabilidad (ISO 55001).
    """
    version_id: str              # UUID único por versión
    cycle_name: str              # Ej. "PAI 2026-2030", "PAI_Q1_2026"
    created_at: str              # ISO 8601 timestamp
    author: str                  # Analista/coordinador que aprobó el plan
    description: str             # Notas del ciclo (cambios respecto a versión anterior)
    snapshot: dict               # Serialización del EnterprisePortfolioResult
    signature: str               # SHA-256 del snapshot serializado (integridad)
    # Métricas de resumen para comparación rápida
    n_projects: int = 0
    total_vnt_COP: float = 0.0
    total_capex_COP: float = 0.0
    solver_status: str = ""
    n_committed: int = 0
    capex_committed_COP: float = 0.0


class PlanVersionStore:
    """Repositorio de versiones de plan PAI con historial append-only.

    Implementa el patrón de planificación continua de Copperleaf:
    cada ciclo PAI genera una nueva versión sin sobrescribir las anteriores.
    El historial permite auditar decisiones de inversión (ISO 55001 §4.5).

    Almacenamiento: JSON local (mismo patrón que SnapshotStore).
    Sin integración EAM/ERP — los planes son autocontenidos.
    """

    def __init__(self, store_path: str = "plan_versions.json"):
        self.store_path = Path(store_path)
        self._versions: List[PlanVersion] = []
        self._load()

    def _load(self) -> None:
        """Carga versiones existentes desde disco."""
        if not self.store_path.exists():
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for v in data.get("versions", []):
                self._versions.append(PlanVersion(**v))
        except Exception:
            pass

    def _save(self) -> None:
        """Persiste el historial en disco (append-only)."""
        import dataclasses
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(
                {"versions": [dataclasses.asdict(v) for v in self._versions]},
                f, ensure_ascii=False, indent=2
            )

    def _sign(self, snapshot: dict) -> str:
        """SHA-256 del snapshot JSON para garantizar integridad."""
        raw = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def save_version(
        self,
        result: "EnterprisePortfolioResult",
        cycle_name: str,
        author: str,
        description: str = "",
    ) -> PlanVersion:
        """Guarda un nuevo snapshot del plan optimizado.

        Args:
            result:      Output de EnterpriseOptimizer.optimize().
            cycle_name:  Nombre del ciclo PAI (ej. "PAI 2026-2030").
            author:      Nombre del analista que aprueba el plan.
            description: Notas del ciclo (cambios, restricciones nuevas, etc.).

        Returns:
            PlanVersion con la firma SHA-256 del snapshot.
        """
        import dataclasses
        snapshot = dataclasses.asdict(result)
        sig = self._sign(snapshot)
        # Métricas de resumen desde committed_summary si existe
        cs = getattr(result, "committed_summary", {}) or {}
        v = PlanVersion(
            version_id=str(uuid.uuid4()),
            cycle_name=cycle_name,
            created_at=datetime.now(timezone.utc).isoformat(),
            author=author,
            description=description,
            snapshot=snapshot,
            signature=sig,
            n_projects=len(result.selected),
            total_vnt_COP=result.total_net_value,
            total_capex_COP=result.total_capex,
            solver_status=result.solver_status,
            n_committed=cs.get("n_committed", 0),
            capex_committed_COP=cs.get("capex_committed_COP", 0.0),
        )
        self._versions.append(v)
        self._save()
        return v

    def save_draft(
        self,
        projects: list,
        cycle_name: str,
        author: str,
        description: str = "",
    ) -> PlanVersion:
        """Guarda un borrador de plan a partir de una lista cruda de ProjectInput.

        A diferencia de save_version() (que requiere el resultado MILP completo),
        save_draft() acepta cualquier colección de proyectos y construye un
        snapshot simplificado. Útil para versionar propuestas preliminares antes
        de la optimización.

        Args:
            projects:    Lista de ProjectInput (o dicts con campos id/name/capex).
            cycle_name:  Nombre del ciclo PAI (ej. "Borrador PAI 2026").
            author:      Analista que genera el borrador.
            description: Notas adicionales.

        Returns:
            PlanVersion con solver_status="DRAFT".
        """
        # Serializar lista de proyectos de forma tolerante
        def _ser(p):
            if hasattr(p, "__dict__"):
                d = {k: v for k, v in p.__dict__.items() if not k.startswith("_")}
            elif isinstance(p, dict):
                d = p
            else:
                d = {"raw": str(p)}
            # Asegurar serializable
            return json.loads(json.dumps(d, default=str))

        snap_projects = [_ser(p) for p in projects]
        total_capex = 0.0
        for p in projects:
            if hasattr(p, "costs") and hasattr(p.costs, "capex_COP"):
                total_capex += p.costs.capex_COP
            elif isinstance(p, dict):
                total_capex += float(p.get("capex_COP", p.get("capex", 0)) or 0)

        snapshot = {
            "selected": snap_projects,
            "total_net_value": 0.0,   # desconocido en borrador
            "total_capex": total_capex,
            "solver_status": "DRAFT",
            "committed_summary": {},
        }
        sig = self._sign(snapshot)
        v = PlanVersion(
            version_id=str(uuid.uuid4()),
            cycle_name=cycle_name,
            created_at=datetime.now(timezone.utc).isoformat(),
            author=author,
            description=f"[BORRADOR] {description}",
            snapshot=snapshot,
            signature=sig,
            n_projects=len(projects),
            total_vnt_COP=0.0,
            total_capex_COP=total_capex,
            solver_status="DRAFT",
            n_committed=0,
            capex_committed_COP=0.0,
        )
        self._versions.append(v)
        self._save()
        return v

    def list_versions(self) -> List[PlanVersion]:
        """Devuelve todas las versiones ordenadas de más reciente a más antigua."""
        return sorted(self._versions,
                      key=lambda v: v.created_at, reverse=True)

    def get_version(self, version_id: str) -> Optional[PlanVersion]:
        """Busca una versión por su ID único."""
        return next((v for v in self._versions if v.version_id == version_id), None)

    def diff_versions(
        self,
        version_id_a: str,
        version_id_b: str,
    ) -> dict:
        """Compara dos versiones del plan y retorna las diferencias clave.

        Compara: proyectos añadidos/eliminados, cambios de año, VNT y CAPEX
        total, cambios en el gasto comprometido.

        Returns:
            dict con: {
                added   → proyectos en B que no estaban en A,
                removed → proyectos en A que no están en B,
                year_changes → proyectos cuyo año de ejecución cambió,
                vnt_delta_COP, capex_delta_COP, n_delta,
                committed_delta
            }
        """
        va = self.get_version(version_id_a)
        vb = self.get_version(version_id_b)
        if not va or not vb:
            return {"error": "Versión no encontrada"}

        sel_a = {s["project_id"]: s for s in va.snapshot.get("selected", [])}
        sel_b = {s["project_id"]: s for s in vb.snapshot.get("selected", [])}

        added   = [sel_b[pid] for pid in sel_b if pid not in sel_a]
        removed = [sel_a[pid] for pid in sel_a if pid not in sel_b]
        year_changes = []
        for pid in sel_a:
            if pid in sel_b and sel_a[pid].get("year") != sel_b[pid].get("year"):
                year_changes.append({
                    "project_id":   pid,
                    "project_name": sel_a[pid].get("project_name", ""),
                    "year_a":       sel_a[pid].get("year"),
                    "year_b":       sel_b[pid].get("year"),
                })

        return {
            "version_a":       va.cycle_name,
            "version_b":       vb.cycle_name,
            "added":           added,
            "removed":         removed,
            "year_changes":    year_changes,
            "vnt_delta_COP":   vb.total_vnt_COP   - va.total_vnt_COP,
            "capex_delta_COP": vb.total_capex_COP  - va.total_capex_COP,
            "n_delta":         vb.n_projects        - va.n_projects,
            "committed_delta": vb.n_committed        - va.n_committed,
        }


# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO B — MOTOR DE DEGRADACIÓN PREDICTIVO
# ══════════════════════════════════════════════════════════════════════════════

class DegradationModel:
    """Enum de modelos de degradación disponibles."""
    WEIBULL    = "weibull"      # Equipos eléctricos/mecánicos (bathtub curve)
    LINEAR     = "linear"       # Infraestructura civil (tubería, obra civil)
    STEP       = "step"         # Activos digitales (obsolescencia discreta)
    ACCELERATED = "accelerated" # Activos en condición crítica ya detectada


@dataclass
class AssetProfile:
    """
    Perfil de un activo físico para el motor de degradación.

    El motor calcula la curva PoF(t) — Probabilidad de Falla vs. Tiempo —
    y determina el año de Economic End-of-Life (EEOL).
    """
    asset_id: str
    asset_name: str
    asset_class: str                        # "transformador", "tuberia", "subestacion", etc.
    business_unit: str                      # TyD | AyA | Gas | Generacion
    degradation_model: str = DegradationModel.WEIBULL
    install_year: int = 2000
    design_life_years: int = 30             # Vida útil de diseño
    current_condition: float = 0.5         # 0.0=nuevo, 1.0=falla inminente
    replacement_cost_COP: float = 0.0       # CAPEX de reemplazo
    annual_maintenance_COP: float = 0.0     # OPEX anual actual
    consequence_level: int = 0
    """Nivel L1-L5 de consecuencia de falla. 0 = no declarado.

    El centinela es 0 y no 3 a propósito: con 3 por defecto era imposible
    distinguir "el analista evaluó y dio Moderado" de "nadie lo evaluó", y el
    motor terminaba pisando evaluaciones reales con el valor del config.
    Sin declarar, el nivel lo define degradation_engine.consequence_by_class.
    """
    # Parámetros específicos de cada modelo
    weibull_beta: float = 2.5               # Shape: <1 infant mortality, >1 wear-out
    weibull_eta: float = 0.0                # Scale (0=auto-calcular desde design_life)
    step_intervals: List[int] = field(default_factory=lambda: [5, 10, 15])
    step_pof_values: List[float] = field(default_factory=lambda: [0.05, 0.25, 0.70])
    acceleration_factor: float = 2.0       # Para modelo acelerado


@dataclass
class DegradationResult:
    """Resultado completo del análisis de degradación para un activo."""
    asset_id: str
    asset_name: str
    current_age_years: float
    current_pof: float                      # PoF hoy
    pof_horizon: Dict[int, float]           # {año: PoF}
    risk_horizon: Dict[int, float]          # {año: Riesgo_anual_COP}
    eeol_year: int                          # Economic End-of-Life
    eeol_pof: float                         # PoF en EEOL
    cumulative_risk_npv: float              # Riesgo acumulado NPV si NO se interviene
    optimal_intervention_year: int          # Año de mínimo costo total
    maintenance_cost_horizon: Dict[int, float]  # OPEX si continúa como está
    intervention_urgency: str               # "INMEDIATA"|"ALTA"|"MEDIA"|"BAJA"
    degradation_model: str
    narrative: str                          # Explicación en lenguaje natural
    annual_failure_prob: Dict[int, float] = field(default_factory=dict)
    """{año: P(falla ese año | sobrevivió)}. Es la cantidad que multiplica la
    consecuencia para dar el riesgo anual. Distinta de pof_horizon, que es
    acumulada — mezclarlas hacía que la falla se contara varias veces."""
    eeol_is_horizon_edge: bool = False
    """True cuando eeol_year es el borde del horizonte y no un quiebre real."""
    eeol_reason: str = ""
    consequence_level_used: int = 0
    consequence_value_COP: float = 0.0
    """Consecuencia efectivamente usada, tomada de risk_matrix. Se expone para
    poder auditar que coincide con la que usa el motor de valor."""


class AssetDegradationEngine:
    """
    Motor Predictivo de Degradación de Activos.
    Lee parámetros (beta Weibull, consecuencia por clase) desde config.json.

    Calcula la curva de Probabilidad de Falla vs. Tiempo para cada activo
    y determina el año óptimo de intervención desde la perspectiva del
    costo total del ciclo de vida (TOTEX mínimo).

    Modelos implementados:
    ─────────────────────
    1. WEIBULL — Equipos eléctricos y electromecánicos
       PoF(t) = 1 - exp(-(t/eta)^beta)
       beta < 1: mortalidad infantil (equipos nuevos defectuosos)
       beta = 1: tasa de falla constante (fallas aleatorias)
       beta > 1: desgaste (la mayoría de activos en envejecimiento)
       beta típico T&D: 2.5-3.5 (transformadores, interruptores)

    2. LINEAR — Infraestructura civil
       PoF(t) = min(t / design_life, 1.0)
       Aplica a: tuberías, obra civil, estructuras metálicas

    3. STEP — Activos digitales y tecnológicos
       PoF salta en años definidos (versiones de software, obsolescencia)
       Aplica a: SCADA, medidores AMI, relés de protección

    4. ACCELERATED — Activos en condición crítica ya detectada
       PoF(t) = PoF_weibull(t) ^ (1/acceleration_factor)
       Aplica cuando: inspección reciente detectó deterioro severo

    Economic End-of-Life (EEOL):
    ─────────────────────────────
    El EEOL es el año en que el costo de mantener el activo en servicio
    supera el costo de reemplazarlo + el riesgo acumulado.
    Es el punto de quiebre de la curva de costo total del ciclo de vida.
    """

    # Tabla de respaldo. NO es la fuente institucional: solo se usa cuando no
    # hay risk_matrix en el config (pruebas sin configuración). La fuente real
    # es risk_matrix.consequence_levels_by_bu — ver _consequence_value_COP().
    CONSEQUENCE_VALUES = {
        1: 50_000_000,       # L1 Muy Menor
        2: 250_000_000,      # L2 Menor
        3: 1_000_000_000,    # L3 Moderado
        4: 5_000_000_000,    # L4 Mayor
        5: 25_000_000_000,   # L5 Catastrófico
    }

    # Respaldo si el config no define degradation_engine.opex_growth_rate
    OPEX_GROWTH_RATE = 0.08

    def __init__(self, wacc: float = 0.10, horizon_years: int = 10,
                 config: dict = None, config_path: str = None):
        """
        Args:
            wacc, horizon_years: parámetros financieros
            config:      dict de configuración completo del motor
            config_path: ruta a config.json — tiene precedencia
        """
        self.wacc = wacc
        self.horizon = horizon_years
        self._factor_npv = (1 - (1 + wacc)**(-horizon_years)) / wacc
        # Cargar config externo si se proporciona
        if config_path:
            import json
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        # Se conserva el config COMPLETO, no solo el sub-bloque. La versión
        # anterior guardaba únicamente degradation_engine y luego intentaba
        # llegar a la matriz de riesgo por una clave '_parent_config' que nadie
        # poblaba nunca: el resultado era que la consecuencia de la falla salía
        # siempre de la tabla del código, desconectada de la matriz institucional.
        self._config   = config or {}
        self._deg_cfg  = self._config.get("degradation_engine", {})
        self._opex_growth = float(
            self._deg_cfg.get("opex_growth_rate", self.OPEX_GROWTH_RATE))

    # ── Consecuencia monetaria de la falla ───────────────────────────────────

    def _consequence_value_COP(self, level: int, business_unit: str = "") -> float:
        """Valor monetario de una falla de nivel `level` para ese negocio.

        Fuente única: risk_matrix del config, la misma que usa ValueEngine.
        Jerarquía: consequence_levels_by_bu[BU] > consequence_levels > respaldo.
        """
        if self._deg_cfg.get("consequence_source", "risk_matrix") == "risk_matrix":
            rm  = self._config.get("risk_matrix", {}) or {}
            key = str(int(level))
            for tabla in (
                (rm.get("consequence_levels_by_bu", {}) or {}).get(business_unit, {}),
                rm.get("consequence_levels", {}) or {},
            ):
                nivel = tabla.get(key)
                if isinstance(nivel, dict) and nivel.get("value_COP"):
                    return float(nivel["value_COP"])
                if isinstance(nivel, (int, float)) and nivel:
                    return float(nivel)
        return float(self.CONSEQUENCE_VALUES.get(int(level), 1_000_000_000))

    def _pof_weibull(self, age: float, eta: float, beta: float) -> float:
        """Función de Weibull: PoF = 1 - exp(-(age/eta)^beta)."""
        if eta <= 0 or age < 0:
            return 0.0
        return 1.0 - math.exp(-((age / eta) ** beta))

    def _hazard_weibull(self, age: float, eta: float, beta: float) -> float:
        """Tasa de riesgo instantánea de Weibull: h(t) = (beta/eta) * (t/eta)^(beta-1).

        Por qué existe, y no se usa la CDF: el defecto de "óptimo de esquina" en
        optimal_intervention_year.
        _pof_weibull() (la CDF) responde "¿cuál es la probabilidad de que el activo
        ya haya fallado a esta edad?" y se satura en 1.0 — es la magnitud correcta
        para current_pof/eeol (estado de salud), pero NO para el costo de riesgo
        ANUAL RECURRENTE de mantener el activo un año más en servicio.
        h(t) responde la pregunta correcta para eso: "¿qué tan probable es que
        falle *este año*, dado que sobrevivió hasta ahora?". Para beta>1 (desgaste),
        h(t) crece sin techo con la edad — a diferencia de la CDF, nunca se satura.
        Se usa exclusivamente en _optimal_year_by_marginal_crossing(); no reemplaza
        _pof_weibull() en pof_horizon/current_pof/eeol_year (esos cálculos siguen
        siendo correctos con la CDF).
        """
        return ValueEngine._tasa_falla_anual(age, eta, beta)

    def _optimal_year_by_marginal_crossing(
        self, profile: AssetProfile, analysis_year: int,
        capex_inflation_rate: float = 0.04,
    ) -> int:
        """Año óptimo de intervención por cruce marginal (metodología Copperleaf VDM).

        Por qué no se busca el "mínimo TOTEX" sobre una
        ventana de escaneo arbitraria (defecto confirmado: cuando WACC >
        capex_inflation_rate y el riesgo se mide con la CDF de PoF —que se
        satura en 1—, nada en el TOTEX descontado crece más rápido que el
        descuento, y el "mínimo" cae siempre en el último año escaneado,
        cambiando con horizon_years sin que cambie el activo).

        Regla: se compara, año a año, el costo marginal de mantener el activo
        un año más en servicio (riesgo vía tasa de riesgo instantánea +
        OPEX) contra el ahorro marginal de diferir el CAPEX un año más
        (beneficio financiero neto de esperar). El año óptimo es el primero
        donde el costo de esperar supera el ahorro de esperar — un cruce,
        no un mínimo global. Por construcción, es insensible a cuántos años
        se escaneen hacia adelante, porque h(t) no tiene techo para beta>1.
        """
        eta = self._get_weibull_eta(profile)
        beta = self._get_weibull_beta(profile)
        consequence_level = self._get_consequence_level(profile)
        consequence_value = self._consequence_value_COP(
            consequence_level, profile.business_unit)
        replacement = profile.replacement_cost_COP

        # Ahorro marginal de diferir el CAPEX un año: costo de capital que se
        # evita pagar ya, neto de la escalación del CAPEX por espera.
        annual_deferral_benefit = replacement * max(self.wacc - capex_inflation_rate, 0.0)

        if annual_deferral_benefit <= 0 or replacement <= 0:
            # Si diferir no ahorra nada (WACC <= inflación CAPEX) o no hay
            # costo de reemplazo definido, no hay incentivo a esperar: actuar ya.
            return analysis_year

        for yr in range(analysis_year, analysis_year + self.horizon + 1):
            age = yr - profile.install_year
            opex_yr = self._opex_at_age(profile, age)
            hazard = self._hazard_weibull(max(age, 0.01), eta, beta)
            annual_risk_cost = hazard * consequence_value
            if (annual_risk_cost + opex_yr) >= annual_deferral_benefit:
                return yr

        # No cruza dentro del horizonte: el activo es seguro dentro de la
        # ventana de planeación; se reporta el borde del horizonte como
        # límite informativo, no como una fecha de intervención real.
        return analysis_year + self.horizon

    def _pof_linear(self, age: float, design_life: float) -> float:
        """Degradación lineal: PoF = age / design_life."""
        return min(age / design_life, 1.0) if design_life > 0 else 1.0

    def _pof_step(self, age: float, intervals: List[int],
                  pof_values: List[float]) -> float:
        """Degradación escalonada: PoF salta en años definidos."""
        pof = 0.0
        for threshold, val in zip(intervals, pof_values):
            if age >= threshold:
                pof = val
        return pof

    def _get_weibull_beta(self, profile: AssetProfile) -> float:
        """
        Devuelve beta Weibull para la clase de activo.
        Prioridad: (1) profile.weibull_beta si fue definido explícitamente,
                   (2) config_v3.degradation_engine.weibull_beta_by_class,
                   (3) default 2.5.
        Soporta las dos formas del dato: escalar suelto, u objeto
        {"beta": 2.0, "_eta_años": 55, "_fuente": "..."}.
        """
        # El perfil gana cuando declara un beta distinto del valor por defecto
        # del dataclass. La versión anterior consultaba un atributo
        # `_weibull_beta_explicit` que NADIE asigna en todo el proyecto, y su
        # fallback (`x if x != 2.5 else 2.5`) devolvía lo mismo en las dos ramas.
        if profile.weibull_beta and profile.weibull_beta != 2.5:
            return float(profile.weibull_beta)
        # Buscar en config por clase de activo
        beta_cfg = self._deg_cfg.get("weibull_beta_by_class", {})
        if profile.asset_class in beta_cfg:
            val = beta_cfg[profile.asset_class]
            # +: valor es dict {"beta": 2.0, "_eta_años": 55,...}
            if isinstance(val, dict):
                return float(val["beta"])
            return float(val)
        return float(profile.weibull_beta or 2.5)

    def _get_weibull_eta(self, profile: AssetProfile) -> float:
        """
        Devuelve eta (vida característica, años) para la clase de activo.
        Prioridad: (1) profile.weibull_eta si > 0,
                   (2) config.degradation_engine.weibull_beta_by_class[clase]._eta_años,
                   (3) profile.design_life_years.
        Solo aplica a la forma de objeto (dict con _eta_años).
        """
        if profile.weibull_eta > 0:
            return profile.weibull_eta
        beta_cfg = self._deg_cfg.get("weibull_beta_by_class", {})
        if profile.asset_class in beta_cfg:
            val = beta_cfg[profile.asset_class]
            if isinstance(val, dict) and "_eta_años" in val:
                eta = float(val["_eta_años"])
                if eta > 0:
                    return eta
        return profile.design_life_years

    def _get_consequence_level(self, profile: AssetProfile) -> int:
        """
        Devuelve nivel de consecuencia para la clase de activo.
        Prioridad: (1) profile.consequence_level si fue definido > 0,
                   (2) config_v3.degradation_engine.consequence_by_class,
                   (3) default 3.
        """
        # El perfil manda cuando declara un nivel; si no lo declara (0), decide
        # el config por clase de activo. La versión anterior usaba el valor 3
        # como centinela de "no declarado", así que un nivel Moderado puesto a
        # propósito —el más frecuente— era pisado silenciosamente por el config.
        # El centinela ahora es 0, que no es un nivel válido de la escala 1-5.
        declarado = getattr(profile, "consequence_level", 0) or 0
        if 1 <= declarado <= 5:
            return int(declarado)
        cons_cfg = self._deg_cfg.get("consequence_by_class", {})
        if profile.asset_class in cons_cfg:
            try:
                return int(cons_cfg[profile.asset_class])
            except (TypeError, ValueError):
                pass
        return 3

    def _pof_at_age(self, profile: AssetProfile, age: float) -> float:
        """Calcula PoF para la edad dada según el modelo del activo."""
        if profile.degradation_model == DegradationModel.WEIBULL:
            eta  = self._get_weibull_eta(profile)     # config._eta_años → profile.weibull_eta → design_life
            beta = self._get_weibull_beta(profile)    # MEDIA-6: desde config
            base_pof = self._pof_weibull(age, eta, beta)

        elif profile.degradation_model == DegradationModel.LINEAR:
            base_pof = self._pof_linear(age, profile.design_life_years)

        elif profile.degradation_model == DegradationModel.STEP:
            base_pof = self._pof_step(age, profile.step_intervals,
                                       profile.step_pof_values)

        elif profile.degradation_model == DegradationModel.ACCELERATED:
            # Usa la calibración del config igual que WEIBULL. La versión
            # anterior leía profile.weibull_eta/beta directo, así que un activo
            # en deterioro crítico —justo el que más importa acertar— ignoraba
            # los parámetros calibrados por clase de activo.
            eta  = self._get_weibull_eta(profile)
            beta = self._get_weibull_beta(profile)
            weibull_pof = self._pof_weibull(age, eta, beta)
            # Aceleración: PoF sube más rápido — eleva al inverso del factor
            af = max(0.1, profile.acceleration_factor)
            base_pof = weibull_pof ** (1.0 / af)

        else:
            base_pof = self._pof_linear(age, profile.design_life_years)

        # Piso por condición observada: una inspección que declara el activo en
        # mal estado no puede quedar por debajo de lo que dice el modelo de edad.
        # El factor sale de config; con 1.0 una condición de 0.8 impone PoF ≥ 0.8.
        # Antes estaba fijo en 0.5, así que un activo declarado en "falla
        # inminente" (condición 1.0) solo llegaba a PoF 0.5 — la mitad de lo que
        # afirmaba la inspección que lo declaró.
        anchor = float(self._deg_cfg.get("condition_pof_anchor", 1.0))
        return min(1.0, max(base_pof, profile.current_condition * anchor))

    def _annual_failure_prob(self, profile: AssetProfile, age: float) -> float:
        """Probabilidad de que el activo falle DURANTE ese año, dado que llegó vivo.

        Es la cantidad correcta para un costo de riesgo anual recurrente, y es
        distinta de la PoF acumulada que devuelve _pof_at_age().

        Por qué importa: _pof_at_age() es la CDF — "¿cuál es la probabilidad de
        que este activo ya haya fallado a esta edad?". Multiplicarla por la
        consecuencia y sumar año a año equivale a suponer que el activo falla
        una y otra vez: en un transformador de 41 años con β=2.5 la suma de la
        CDF sobre 10 años daba 8,3, es decir el riesgo acumulado valía más de
        cinco veces la consecuencia de una sola falla.

        Aquí se usa la probabilidad condicional del intervalo:

            P(falla en [t, t+1] | sobrevivió hasta t) = 1 − S(t+1)/S(t)

        que nunca supera 1 y, sumada sobre el horizonte, no puede exceder la
        probabilidad de haber fallado alguna vez.
        """
        s_now  = 1.0 - min(self._pof_at_age(profile, age), 1.0)
        s_next = 1.0 - min(self._pof_at_age(profile, age + 1.0), 1.0)
        if s_now <= 1e-12:
            # Ya se considera fallado: el riesgo del año es el total, no más.
            return 1.0
        return max(0.0, min(1.0, 1.0 - (s_next / s_now)))

    def _annual_risk_COP(self, profile: AssetProfile, age: float) -> float:
        """Costo de riesgo esperado del año = P(falla ese año) × consecuencia."""
        level = self._get_consequence_level(profile)
        return (self._annual_failure_prob(profile, age)
                * self._consequence_value_COP(level, profile.business_unit))

    def _opex_at_age(self, profile: AssetProfile, age: float) -> float:
        """OPEX proyectado considerando crecimiento por degradación.

        Un solo término de crecimiento. La versión anterior sumaba
        `0.08 × edad` MÁS `PoF × 2.0`, y como la PoF es a su vez función de la
        edad, la degradación entraba dos veces en el mismo multiplicador —
        con dos constantes sin fuente. El resultado inflaba el OPEX proyectado
        de los activos viejos, que son justamente los que se están evaluando
        para reposición.
        """
        base = profile.annual_maintenance_COP
        return base * (1.0 + self._opex_growth * max(age, 0.0))

    def _urgency_label(self, pof_now: float, eeol_year: int,
                       current_year: int) -> str:
        """Determina la urgencia de intervención."""
        years_to_eeol = eeol_year - current_year
        if pof_now > 0.50 or years_to_eeol <= 1:
            return "INMEDIATA"
        elif pof_now > 0.25 or years_to_eeol <= 3:
            return "ALTA"
        elif pof_now > 0.10 or years_to_eeol <= 6:
            return "MEDIA"
        else:
            return "BAJA"

    def analyze(
        self,
        profile: AssetProfile,
        analysis_year: int = None,
    ) -> DegradationResult:
        """
        Analiza la degradación completa de un activo.

        El modelo de degradación se selecciona mediante ``profile.degradation_model``.
        Use las constantes de ``DegradationModel`` para evitar errores tipográficos:

            from epm_core import AssetProfile, AssetDegradationEngine, DegradationModel

            eng = AssetDegradationEngine(wacc=0.10, horizon_years=10)
            profile = AssetProfile(
                ...,
                degradation_model=DegradationModel.WEIBULL,  # "weibull"
            )
            result = eng.analyze(profile)

        Modelos disponibles (ver DegradationModel):
          WEIBULL     — equipos eléctricos/electromecánicos (default)
          LINEAR      — infraestructura civil y tuberías
          STEP        — activos digitales y SCADA
          ACCELERATED — activos con deterioro crítico ya detectado

        Produce:
        — Curva PoF(t) para los próximos `horizon_years` años
        — Riesgo anual en COP para cada año
        — Año de EEOL (Economic End-of-Life)
        — Año óptimo de intervención (mínimo TOTEX)
        — Narrativa en lenguaje natural
        """
        if analysis_year is None:
            analysis_year = datetime.now().year

        age_now = analysis_year - profile.install_year
        pof_now = self._pof_at_age(profile, age_now)

        # Consecuencia efectiva — de risk_matrix, la misma fuente que ValueEngine
        _cons_lvl = self._get_consequence_level(profile)
        _cons_val = self._consequence_value_COP(_cons_lvl, profile.business_unit)

        # Horizonte de análisis
        pof_horizon = {}
        annual_pf = {}
        risk_horizon = {}
        maint_horizon = {}
        cumulative_risk = 0.0

        for yr in range(analysis_year, analysis_year + self.horizon + 1):
            age = yr - profile.install_year
            pof = self._pof_at_age(profile, age)          # acumulada — estado de salud
            pf_yr = self._annual_failure_prob(profile, age)  # anual — para el costo
            risk = self._annual_risk_COP(profile, age)
            opex = self._opex_at_age(profile, age)
            fd = 1.0 / (1 + self.wacc) ** (yr - analysis_year)

            pof_horizon[yr] = round(pof, 4)
            annual_pf[yr] = round(pf_yr, 5)
            risk_horizon[yr] = risk
            maint_horizon[yr] = opex
            cumulative_risk += risk * fd

        # EEOL: año donde costo_acumulado_mantener > costo_reemplazar
        _eeol_thr = float(self._deg_cfg.get("eeol_replacement_threshold", 0.85))
        eeol_year = analysis_year + self.horizon  # borde del horizonte
        eeol_pof = pof_horizon.get(eeol_year, 1.0)
        eeol_found = False
        replacement = profile.replacement_cost_COP

        accumulated_cost = 0.0
        for yr in range(analysis_year, analysis_year + self.horizon + 1):
            age = yr - profile.install_year
            risk = risk_horizon.get(yr, 0)
            opex = maint_horizon.get(yr, 0)
            fd = 1.0 / (1 + self.wacc) ** (yr - analysis_year)
            accumulated_cost += (risk + opex) * fd

            # Punto de quiebre: acumulado supera el costo de reemplazar hoy
            if replacement > 0 and accumulated_cost >= replacement * _eeol_thr:
                eeol_year = yr
                eeol_pof = pof_horizon.get(yr, pof_now)
                eeol_found = True
                break

        # Sin costo de reemplazo declarado el quiebre no se puede calcular, y
        # sin cruce dentro del horizonte tampoco existe. En ambos casos el valor
        # devuelto es el borde de la ventana, no una fecha de fin de vida: hay
        # que marcarlo para que no se lea como un resultado del modelo. Es el
        # mismo defecto de esquina que ya se había corregido para el año óptimo.
        eeol_is_horizon_edge = not eeol_found
        eeol_reason = (
            "" if eeol_found else
            ("sin costo de reemplazo declarado — no se puede calcular el quiebre"
             if replacement <= 0 else
             "el costo de mantener no alcanza el umbral dentro del horizonte")
        )

        # Año óptimo: cruce marginal (riesgo vía tasa instantánea vs. ahorro de
        # diferir CAPEX) — ver _optimal_year_by_marginal_crossing().
        # CORRECCIÓN: el bucle anterior de "mínimo TOTEX" sobre la CDF de
        # PoF producía un óptimo de esquina (siempre analysis_year+horizon,
        # verificado con horizontes de 10/15/25/40a sobre activos de alto y
        # bajo riesgo — el resultado seguía al parámetro horizon_years, no al
        # activo). Se reemplaza por la regla de cruce marginal estándar de
        # timing de inversión (Copperleaf VDM), insensible a horizon_years.
        best_year = self._optimal_year_by_marginal_crossing(profile, analysis_year)

        urgency = self._urgency_label(pof_now, eeol_year, analysis_year)

        # Narrativa
        _dl = profile.design_life_years or 0
        pct_life = min(age_now / _dl * 100, 100) if _dl > 0 else 0.0
        _pf_now = annual_pf.get(analysis_year, 0.0)
        narrative = (
            f"El activo {profile.asset_name} ({profile.asset_class}) tiene "
            f"{age_now:.0f} años de edad ({pct_life:.0f}% de su vida útil de diseño). "
            f"Su probabilidad de falla acumulada es {pof_now*100:.1f}% y la "
            f"probabilidad de fallar durante el próximo año es {_pf_now*100:.1f}%, "
            f"bajo el modelo {profile.degradation_model}. "
            f"Una falla de nivel L{_cons_lvl} vale COP {_cons_val/1e9:.1f}B "
            f"según la matriz de riesgo de {profile.business_unit or 'la organización'}. "
        )
        if urgency == "INMEDIATA":
            narrative += (
                f"ATENCIÓN: El activo ha superado su umbral crítico de PoF. "
                f"El EEOL se estima en {eeol_year} (PoF={eeol_pof*100:.0f}%). "
                f"El riesgo acumulado sin intervención es COP "
                f"{cumulative_risk/1e9:.1f}B en NPV. "
                f"Se recomienda intervención inmediata."
            )
        else:
            _eeol_txt = (f"El EEOL se estima en {eeol_year}. " if eeol_found
                         else f"No se identificó fin de vida económica dentro del "
                              f"horizonte ({eeol_reason}). ")
            narrative += (
                _eeol_txt +
                f"El año óptimo de intervención es {best_year} "
                f"(cruce marginal: costo de riesgo anual vs. ahorro de diferir CAPEX). "
                f"El riesgo acumulado sin intervención es COP "
                f"{cumulative_risk/1e9:.2f}B en NPV. "
                f"Urgencia: {urgency}."
            )

        return DegradationResult(
            asset_id=profile.asset_id,
            asset_name=profile.asset_name,
            current_age_years=age_now,
            current_pof=pof_now,
            pof_horizon=pof_horizon,
            risk_horizon=risk_horizon,
            eeol_year=eeol_year,
            eeol_pof=eeol_pof,
            cumulative_risk_npv=cumulative_risk,
            optimal_intervention_year=best_year,
            maintenance_cost_horizon=maint_horizon,
            intervention_urgency=urgency,
            degradation_model=profile.degradation_model,
            narrative=narrative,
            annual_failure_prob=annual_pf,
            eeol_is_horizon_edge=eeol_is_horizon_edge,
            eeol_reason=eeol_reason,
            consequence_level_used=_cons_lvl,
            consequence_value_COP=_cons_val,
        )

    def analyze_portfolio(
        self,
        profiles: List[AssetProfile],
        analysis_year: int = None,
    ) -> Dict[str, DegradationResult]:
        """Analiza toda una cartera de activos y devuelve resultados indexados."""
        if analysis_year is None:
            analysis_year = datetime.now().year
        return {
            p.asset_id: self.analyze(p, analysis_year)
            for p in profiles
        }

    def to_optimal_years(
        self,
        results: Dict[str, DegradationResult],
    ) -> Dict[str, int]:
        """
        MEDIA-7: Pipeline AssetDegradationEngine → Optimal Timing del motor base.

        Convierte los resultados de degradación en un mapa asset_id → año_óptimo
        que puede usarse para poblar ProjectInput.optimal_year automáticamente.

        Uso:
            deg_results = engine.analyze_portfolio(profiles)
            optimal_years = engine.to_optimal_years(deg_results)
            # Luego al construir ProjectInput:
            project.optimal_year = optimal_years.get(asset_id, current_year)

        El año óptimo viene del EEOL o del optimal_intervention_year,
        según cuál sea más conservador (el menor de los dos).
        """
        mapping = {}
        for asset_id, result in results.items():
            # El año óptimo es el mínimo entre EEOL y optimal_intervention_year
            # EEOL: cuando mantener > reemplazar
            # optimal_intervention_year: mínimo TOTEX
            year = min(result.eeol_year, result.optimal_intervention_year)
            mapping[asset_id] = year
        return mapping

    def enrich_projects(
        self,
        projects: List,   # List[ProjectInput]
        profiles: List[AssetProfile],
        analysis_year: int = None,
    ) -> List:
        """
        MEDIA-7: Enriquece automáticamente ProjectInput con el año óptimo
        calculado por el motor de degradación.

        Vincula perfil de activo → proyecto por asset_id == project.id.
        Si no hay coincidencia, el proyecto no se modifica.

        Uso:
            enriched = deg_engine.enrich_projects(projects, asset_profiles)
            result = optimizer.optimize(enriched, periods)
        """
        if analysis_year is None:
            analysis_year = datetime.now().year
        results = self.analyze_portfolio(profiles, analysis_year)
        optimal_years = self.to_optimal_years(results)

        for project in projects:
            if project.id in optimal_years:
                old_yr = getattr(project, 'optimal_year', analysis_year)
                new_yr = optimal_years[project.id]
                project.optimal_year = new_yr
                # Si el año real de ejecución es antes del óptimo,
                # actualizar también (no se puede ejecutar antes del EEOL)
                if getattr(project, 'execution_year', analysis_year) < new_yr:
                    project.execution_year = new_yr

        return projects

    def print_portfolio_degradation(
        self,
        results: Dict[str, DegradationResult],
    ) -> None:
        """Imprime resumen ejecutivo de degradación del portafolio de activos."""
        sep = "═" * 70
        urgency_order = {"INMEDIATA": 0, "ALTA": 1, "MEDIA": 2, "BAJA": 3}
        sorted_results = sorted(
            results.values(),
            key=lambda r: (urgency_order.get(r.intervention_urgency, 4),
                           -r.current_pof)
        )
        print(f"\n{sep}")
        print(f"  ANÁLISIS DE DEGRADACIÓN — PORTAFOLIO DE ACTIVOS")
        print(sep)
        print(f"\n  {'Urgencia':<12} {'Activo':<30} {'Edad':>6} "
              f"{'PoF%':>7} {'EEOL':>6} {'Interv.':>7} {'Riesgo NPV':>15}")
        print(f"  {'─'*68}")
        for r in sorted_results:
            urg_sym = {"INMEDIATA":"🔴","ALTA":"🟠","MEDIA":"🟡","BAJA":"🟢"}.get(
                r.intervention_urgency, "⚪")
            print(
                f"  {urg_sym} {r.intervention_urgency:<10}  "
                f"{r.asset_name[:28]:<28}  "
                f"{r.current_age_years:>5.0f}a  "
                f"{r.current_pof*100:>6.1f}%  "
                f"{r.eeol_year:>6}  "
                f"{r.optimal_intervention_year:>7}  "
                f"COP {r.cumulative_risk_npv/1e9:>7.2f}B"
            )
        total_risk = sum(r.cumulative_risk_npv for r in results.values())
        inmediatas = sum(1 for r in results.values()
                         if r.intervention_urgency == "INMEDIATA")
        print(f"\n  Riesgo total portafolio (NPV sin intervención): "
              f"COP {total_risk/1e9:.2f}B")
        print(f"  Activos en urgencia INMEDIATA: {inmediatas}")
        print(f"\n{sep}\n")


# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO C — SISTEMA DE SNAPSHOTS ISO 55001 §4.5
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EvaluationSnapshot:
    """
    Snapshot inmutable de una evaluación de proyecto.

    Cumple con ISO 55001:2024 §4.5.5 — Trazabilidad y auditoría:
    — Cada evaluación queda registrada con todos sus inputs y outputs.
    — El hash SHA-256 garantiza que no se puede alterar retroactivamente.
    — El registro es append-only: no existe función de edición.
    — Las comparaciones entre snapshots exponen cambios de criterio.
    """
    snapshot_id: str                 # UUID único e irreversible
    timestamp_utc: str               # ISO 8601 con zona horaria
    analyst: str                     # Usuario que ejecutó la evaluación
    # Versión del modelo en el momento
    config_version: str
    config_hash: str                 # SHA-256 del config.json
    # Identificación del proyecto
    project_id: str
    project_name: str
    business_unit: str
    # Inputs completos
    inputs: dict                     # ProjectInput serializado
    # Outputs completos
    outputs: dict                    # Resultado de evaluate() completo
    # Hash de integridad
    integrity_hash: str              # SHA-256(snapshot_id + inputs + outputs)
    # Hash de contenido — sin UUID/timestamp, útil para deduplicación idempotente
    content_hash: str = ""           # SHA-256(inputs + outputs) — igual si mismos datos
    # Metadatos de auditoría
    session_id: str = ""             # Agrupa snapshots de una misma sesión
    change_reason: str = ""          # Por qué se evaluó (nueva evaluación, recalibración)
    supersedes: str = ""             # snapshot_id que este reemplaza (si aplica)


class SnapshotStore:
    """
    Almacén de snapshots inmutables para trazabilidad ISO 55001 §4.5.

    Características:
    — Append-only: ningún snapshot puede eliminarse ni modificarse.
    — Integridad verificable: SHA-256 de inputs + outputs.
    — Comparación de snapshots: expone qué cambió entre dos evaluaciones.
    — Exportación JSON firmada.
    — Búsqueda por proyecto, analista, fecha, versión de config.

    Uso:
        store = SnapshotStore("./snapshots")
        snap = store.capture(result, project, config, analyst="esteban")
        diff = store.compare(snap_id_1, snap_id_2)
        store.export_audit_report("audit_2026.json")
    """

    def __init__(self, storage_dir: str = "./snapshots"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.storage_dir / "_index.json"
        self._index = self._load_index()
        self._session_id = str(uuid.uuid4())[:8]

    def _load_index(self) -> dict:
        if self._index_path.exists():
            with open(self._index_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"snapshots": [], "created": datetime.now(timezone.utc).isoformat()}

    def _save_index(self) -> None:
        with open(self._index_path, 'w', encoding='utf-8') as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)

    def _hash_dict(self, d: dict) -> str:
        """SHA-256 de un dict serializado canonicamente."""
        canonical = json.dumps(d, sort_keys=True, ensure_ascii=False,
                                default=str)
        return hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    # Campos cubiertos por el hash de integridad. Cambiar esta lista cambia el
    # esquema de firma y obliga a subir INTEGRITY_SCHEMA.
    INTEGRITY_FIELDS = (
        "snapshot_id", "timestamp_utc", "analyst",
        "config_version", "config_hash",
        "project_id", "project_name", "business_unit",
        "inputs", "outputs",
        "session_id", "change_reason", "supersedes",
    )
    INTEGRITY_SCHEMA = "v2"

    @classmethod
    def _integrity_source(cls, **campos) -> dict:
        """Construye el dict canónico que se firma."""
        src = {k: campos.get(k) for k in cls.INTEGRITY_FIELDS}
        src["_schema"] = cls.INTEGRITY_SCHEMA
        return src

    @staticmethod
    def _legacy_integrity_source(snap: dict) -> dict:
        """Esquema v1 — solo snap_id + inputs + outputs.

        Se conserva para poder verificar snapshots capturados antes de ampliar
        la firma. Un snapshot v1 verifica como válido pero se reporta como tal:
        su autor y su fecha nunca estuvieron protegidos y no se puede afirmar
        retroactivamente que lo estén.
        """
        return {
            "snap_id": snap.get("snapshot_id"),
            "inputs":  snap.get("inputs"),
            "outputs": snap.get("outputs"),
        }

    def _serialize_project(self, project: ProjectInput) -> dict:
        """Serializa ProjectInput a dict para el snapshot."""
        def safe_serialize(obj):
            if hasattr(obj, '__dict__'):
                return {k: safe_serialize(v) for k, v in obj.__dict__.items()
                        if not k.startswith('_')}
            elif isinstance(obj, list):
                return [safe_serialize(i) for i in obj]
            elif isinstance(obj, dict):
                return {k: safe_serialize(v) for k, v in obj.items()}
            else:
                return obj
        return safe_serialize(project)

    def capture(
        self,
        evaluation_result: dict,
        project: ProjectInput,
        config: dict,
        analyst: str = "sistema",
        change_reason: str = "Evaluación estándar",
        supersedes: str = "",
    ) -> EvaluationSnapshot:
        """
        Captura un snapshot inmutable de una evaluación.

        Returns:
            EvaluationSnapshot con hash de integridad verificable.
        """
        snap_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        config_copy = {k: v for k, v in config.items()
                       if not k.startswith('_')}
        config_hash = self._hash_dict(config_copy)
        config_version = config.get("_metadata", {}).get("version", "?")

        inputs_dict = self._serialize_project(project)

        # Limpiar outputs para serialización
        outputs_dict = {}
        for k, v in evaluation_result.items():
            try:
                json.dumps(v, default=str)
                outputs_dict[k] = v
            except Exception:
                outputs_dict[k] = str(v)

        # content_hash — hash determinista sobre datos (sin UUID/timestamp)
        # Permite detectar evaluaciones duplicadas con exactamente los mismos inputs/outputs.
        content_source = {
            "inputs": inputs_dict,
            "outputs": outputs_dict,
        }
        content_hash = self._hash_dict(content_source)

        # integrity_hash — cubre el registro COMPLETO, no solo el cálculo.
        #
        # La versión anterior firmaba únicamente snap_id + inputs + outputs, así
        # que analyst, timestamp_utc, change_reason, config_hash y supersedes
        # quedaban fuera: se podía reescribir QUIÉN hizo la evaluación y CUÁNDO,
        # y verify() seguía respondiendo "no fue alterado". Para un registro cuyo
        # propósito declarado es trazabilidad ISO 55001 §4.5 y no repudio, eso
        # dejaba protegido el número y desprotegida la responsabilidad sobre él.
        integrity_source = self._integrity_source(
            snapshot_id=snap_id,
            timestamp_utc=timestamp,
            analyst=analyst,
            config_version=config_version,
            config_hash=config_hash,
            project_id=project.id,
            project_name=project.name,
            business_unit=project.business_unit,
            inputs=inputs_dict,
            outputs=outputs_dict,
            session_id=self._session_id,
            change_reason=change_reason,
            supersedes=supersedes,
        )
        integrity_hash = self._hash_dict(integrity_source)

        snapshot = EvaluationSnapshot(
            snapshot_id=snap_id,
            timestamp_utc=timestamp,
            analyst=analyst,
            config_version=config_version,
            config_hash=config_hash,
            project_id=project.id,
            project_name=project.name,
            business_unit=project.business_unit,
            inputs=inputs_dict,
            outputs=outputs_dict,
            integrity_hash=integrity_hash,
            content_hash=content_hash,
            session_id=self._session_id,
            change_reason=change_reason,
            supersedes=supersedes,
        )

        # Persistir snapshot
        snap_path = self.storage_dir / f"{snap_id}.json"
        with open(snap_path, 'w', encoding='utf-8') as f:
            json.dump({
                "snapshot": {
                    k: v for k, v in snapshot.__dict__.items()
                },
                "_integrity": {
                    "algorithm": "SHA-256",
                    "hash": integrity_hash,
                    "note": "Este archivo es inmutable. Cualquier modificación invalida el hash."
                }
            }, f, ensure_ascii=False, indent=2, default=str)

        # Actualizar índice — encadenado
        # Cada entrada incluye el hash de la anterior. Sin la cadena, el índice
        # es un JSON editable: borrar una evaluación del registro no dejaba
        # rastro, aunque el docstring de la clase afirmara "append-only".
        _prev = (self._index["snapshots"][-1].get("entry_hash", "")
                 if self._index["snapshots"] else "")
        _entry = {
            "snapshot_id": snap_id,
            "timestamp_utc": timestamp,
            "project_id": project.id,
            "project_name": project.name,
            "business_unit": project.business_unit,
            "analyst": analyst,
            "config_version": config_version,
            "net_value_COP": evaluation_result.get("net_value_COP", 0),
            "vei": evaluation_result.get("value_efficiency_index", 0),
            "change_reason": change_reason,
            "supersedes": supersedes,
            "integrity_hash": integrity_hash,
            "prev_hash": _prev,
        }
        _entry["entry_hash"] = self._hash_dict(_entry)
        self._index["snapshots"].append(_entry)
        self._save_index()

        return snapshot

    def verify_index(self) -> Tuple[bool, List[str]]:
        """Verifica la cadena del índice y su correspondencia con los archivos.

        Detecta tres cosas que antes pasaban inadvertidas:
          · entradas alteradas (su hash no corresponde a su contenido),
          · entradas eliminadas o insertadas (la cadena prev_hash se rompe),
          · snapshots en disco sin entrada en el índice, y al revés.

        Returns:
            (True, []) si todo cuadra; (False, [hallazgos]) si no.
        """
        problemas: List[str] = []
        prev = ""
        for i, e in enumerate(self._index.get("snapshots", [])):
            if "entry_hash" not in e:
                problemas.append(
                    f"Entrada {i} ({e.get('snapshot_id','?')}) sin encadenar "
                    f"(registrada antes de la cadena de integridad)."
                )
                prev = ""
                continue
            base = {k: v for k, v in e.items() if k != "entry_hash"}
            if self._hash_dict(base) != e["entry_hash"]:
                problemas.append(f"Entrada {i} ({e.get('snapshot_id','?')}) alterada.")
            if e.get("prev_hash", "") != prev:
                problemas.append(
                    f"Cadena rota en la entrada {i} ({e.get('snapshot_id','?')}): "
                    f"se eliminó o insertó un registro anterior."
                )
            prev = e["entry_hash"]

        en_indice = {e.get("snapshot_id") for e in self._index.get("snapshots", [])}
        en_disco = {f.stem for f in self.storage_dir.glob("*.json")
                    if f.name != "_index.json"}
        for sid in sorted(en_disco - en_indice):
            problemas.append(f"Snapshot {sid} está en disco pero no en el índice.")
        for sid in sorted(en_indice - en_disco):
            problemas.append(f"Snapshot {sid} está en el índice pero falta en disco.")

        return (not problemas), problemas

    def verify(self, snapshot_id: str) -> Tuple[bool, str]:
        """
        Verifica la integridad de un snapshot.

        Returns:
            (True, "OK") si el hash es válido.
            (False, mensaje) si fue alterado.
        """
        snap_path = self.storage_dir / f"{snapshot_id}.json"
        if not snap_path.exists():
            return False, f"Snapshot {snapshot_id} no encontrado"

        with open(snap_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        snap = data.get("snapshot", {})
        stored_hash = snap.get("integrity_hash", "")

        # Esquema vigente: cubre el registro completo (autor, fecha, razón).
        computed_v2 = self._hash_dict(self._integrity_source(**snap))
        if computed_v2 == stored_hash:
            return True, "Integridad verificada — registro completo sin alteración"

        # Esquema anterior: firmaba solo el cálculo. Si coincide, el snapshot es
        # auténtico en sus datos pero su autor y fecha nunca estuvieron firmados;
        # se dice explícitamente en vez de reportar un "OK" que no corresponde.
        computed_v1 = self._hash_dict(self._legacy_integrity_source(snap))
        if computed_v1 == stored_hash:
            return True, (
                "Integridad parcial (esquema v1) — inputs y outputs sin alteración. "
                "Autor, fecha y razón NO están cubiertos por la firma de este "
                "snapshot y no pueden darse por verificados."
            )

        return False, (
            f"⚠ HASH INVÁLIDO — El snapshot fue alterado.\n"
            f"  Almacenado: {stored_hash[:16]}...\n"
            f"  Calculado:  {computed_v2[:16]}..."
        )

    def load(self, snapshot_id: str) -> Optional[dict]:
        """Carga un snapshot completo por ID."""
        snap_path = self.storage_dir / f"{snapshot_id}.json"
        if not snap_path.exists():
            return None
        with open(snap_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def compare(self, snap_id_1: str, snap_id_2: str) -> dict:
        """
        Compara dos snapshots y expone qué cambió.

        Detecta cambios en:
        — Parámetros de entrada (construction_years, CAPEX, riesgo, etc.)
        — Parámetros del config (WACC, precios regulatorios)
        — Resultados (VNT, VEI, penalización temporalidad)

        Útil para responder: "¿Por qué cambió el VNT de este proyecto
        entre la evaluación de enero y la de abril?"
        """
        s1 = self.load(snap_id_1)
        s2 = self.load(snap_id_2)

        if not s1 or not s2:
            return {"error": "Uno o ambos snapshots no encontrados"}

        snap1 = s1.get("snapshot", {})
        snap2 = s2.get("snapshot", {})

        def diff_dicts(d1: dict, d2: dict, prefix: str = "") -> List[dict]:
            changes = []
            all_keys = set(d1.keys()) | set(d2.keys())
            for k in sorted(all_keys):
                path = f"{prefix}.{k}" if prefix else k
                v1, v2 = d1.get(k), d2.get(k)
                if isinstance(v1, dict) and isinstance(v2, dict):
                    changes.extend(diff_dicts(v1, v2, path))
                elif v1 != v2:
                    changes.append({
                        "field": path,
                        "snapshot_1": v1,
                        "snapshot_2": v2,
                        "type": "modificado" if (v1 is not None and v2 is not None)
                                else ("añadido" if v1 is None else "eliminado"),
                    })
            return changes

        input_changes  = diff_dicts(snap1.get("inputs", {}),
                                     snap2.get("inputs", {}), "inputs")
        output_changes = diff_dicts(snap1.get("outputs", {}),
                                     snap2.get("outputs", {}), "outputs")

        # Cambio de VNT y VEI (los más importantes)
        vnt1 = snap1.get("outputs", {}).get("net_value_COP", 0)
        vnt2 = snap2.get("outputs", {}).get("net_value_COP", 0)
        vei1 = snap1.get("outputs", {}).get("value_efficiency_index", 0)
        vei2 = snap2.get("outputs", {}).get("value_efficiency_index", 0)

        return {
            "snapshot_1": {
                "id": snap_id_1,
                "timestamp": snap1.get("timestamp_utc"),
                "analyst": snap1.get("analyst"),
                "config_version": snap1.get("config_version"),
                "config_hash": snap1.get("config_hash", "")[:12] + "...",
            },
            "snapshot_2": {
                "id": snap_id_2,
                "timestamp": snap2.get("timestamp_utc"),
                "analyst": snap2.get("analyst"),
                "config_version": snap2.get("config_version"),
                "config_hash": snap2.get("config_hash", "")[:12] + "...",
            },
            "summary": {
                "vnt_delta_COP": vnt2 - vnt1,
                "vnt_delta_pct": (vnt2 - vnt1) / abs(vnt1) * 100 if vnt1 != 0 else None,
                "vei_delta": vei2 - vei1,
                "n_input_changes": len(input_changes),
                "n_output_changes": len(output_changes),
                "config_changed": snap1.get("config_hash") != snap2.get("config_hash"),
            },
            "input_changes": input_changes,
            "output_changes": [c for c in output_changes
                                if c["field"] in (
                                    "outputs.net_value_COP",
                                    "outputs.value_efficiency_index",
                                    "outputs.benefits_bruto_COP",
                                    "outputs.benefits_ajustados_COP",
                                    "outputs.penalizacion_temporalidad_COP",
                                    "outputs.delta_r_npv_COP",
                                )],
        }

    def search(
        self,
        project_id: str = None,
        analyst: str = None,
        config_version: str = None,
        from_date: str = None,
        to_date: str = None,
    ) -> List[dict]:
        """Busca snapshots con filtros múltiples."""
        results = self._index.get("snapshots", [])
        if project_id:
            results = [s for s in results if s.get("project_id") == project_id]
        if analyst:
            results = [s for s in results if s.get("analyst") == analyst]
        if config_version:
            results = [s for s in results
                       if s.get("config_version") == config_version]
        if from_date:
            results = [s for s in results
                       if s.get("timestamp_utc", "") >= from_date]
        if to_date:
            results = [s for s in results
                       if s.get("timestamp_utc", "") <= to_date]
        return sorted(results, key=lambda s: s.get("timestamp_utc", ""),
                      reverse=True)

    def export_audit_report(self, output_path: str) -> str:
        """
        Exporta reporte de auditoría completo en JSON firmado.
        Incluye todos los snapshots + verificación de integridad de cada uno.
        """
        report = {
            "_report_metadata": {
                "generated": datetime.now(timezone.utc).isoformat(),
                "standard": "ISO 55001:2024 §4.5.5",
                "total_snapshots": len(self._index.get("snapshots", [])),
                "storage_dir": str(self.storage_dir),
            },
            "snapshots": [],
        }

        for entry in self._index.get("snapshots", []):
            sid = entry.get("snapshot_id")
            valid, msg = self.verify(sid)
            report["snapshots"].append({
                **entry,
                "integrity_valid": valid,
                "integrity_message": msg,
            })

        report_hash = self._hash_dict(report)
        report["_report_integrity"] = {
            "hash": report_hash,
            "algorithm": "SHA-256",
        }

        path = Path(output_path)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)

        return str(path)

    def print_summary(self) -> None:
        """Imprime resumen del almacén de snapshots."""
        snaps = self._index.get("snapshots", [])
        sep = "═" * 70
        print(f"\n{sep}")
        print(f"  SNAPSHOT STORE — ISO 55001:2024 §4.5 — {len(snaps)} registros")
        print(sep)
        if not snaps:
            print("  Sin snapshots registrados.")
        else:
            print(f"\n  {'Timestamp':<28} {'Proyecto':<22} {'Analista':<14} "
                  f"{'VNT':>12} {'Config':>8}")
            print(f"  {'─'*68}")
            for s in snaps[-10:]:  # últimos 10
                print(
                    f"  {s.get('timestamp_utc','')[:19]:<28}  "
                    f"{s.get('project_id','')[:20]:<22}  "
                    f"{s.get('analyst','')[:12]:<14}  "
                    f"COP {s.get('net_value_COP',0)/1e6:>8.0f}MM  "
                    f"v{s.get('config_version','?'):>6}"
                )
        print(f"\n{sep}\n")


# ══════════════════════════════════════════════════════════════════════════════
# DEMO Y SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════



# ══════════════════════════════════════════════════════════════════════════════
# BLOQUE 11 — PORTFOLIO LOADER (Excel / CSV → ProjectInput[])
# ══════════════════════════════════════════════════════════════════════════════

# Mapas de nombres de columna → campo interno
# Acepta variaciones con espacios, mayúsculas, guiones bajos
_COL_ALIASES: Dict[str, List[str]] = {
    # ── Identificación ────────────────────────────────────────────────────────
    "id":   [
        "id","codigo","code","pei","project_id","proyecto_id","iniciativa",
        "id iniciativa","id_iniciativa",
        "codigo proyecto","código proyecto",
        "codigo proyecto banco pei","código proyecto banco pei",
        "codigo_proyecto_banco_pei",
    ],
    "name": [
        "name","nombre","descripcion","descripción","project_name",
        "nombre proyecto","nombre_proyecto","nombre proyecto pei",
        "nombre iniciativa","nombre_iniciativa",
    ],
    "business_unit": [
        "business_unit","bu","negocio","segmento","segment",
        "unidad de negocio","unidad_negocio","bu epm",
    ],
    "investment_type": [
        "investment_type","tipo","tipo inversion","tipo_inversion",
        "tipo inversión","tipo_inversión","category","categoría",
    ],
    # ── CAPEX ─────────────────────────────────────────────────────────────────
    "capex_total": [
        "capex_total","capex total","capex total mm","total capex",
        "inversion total","inversión total","inversión_total",
        # Alias comunes en exportaciones EPM / AIP
        "capex_cop","capex (cop)","capex cop","capex_total_cop",
        "capex total cop","inversion_cop","inversión cop",
        "costo total","costo_total","costo total cop",
    ],
    "capex_2026": [
        "capex_2026","capex 2026","2026","cap_2026",
        "capex 2026 (mm)","capex 2026 (mm cop)","capex_2026_mm",
    ],
    "capex_2027": [
        "capex_2027","capex 2027","2027","cap_2027",
        "capex 2027 (mm)","capex 2027 (mm cop)","capex_2027_mm",
    ],
    "capex_2028": [
        "capex_2028","capex 2028","2028","cap_2028",
        "capex 2028 (mm)","capex 2028 (mm cop)","capex_2028_mm",
    ],
    "capex_2029": [
        "capex_2029","capex 2029","2029","cap_2029",
        "capex 2029 (mm)","capex 2029 (mm cop)","capex_2029_mm",
    ],
    "capex_2030": [
        "capex_2030","capex 2030","2030","cap_2030",
        "capex 2030 (mm)","capex 2030 (mm cop)","capex_2030_mm",
    ],
    # ── VNT / VEI ─────────────────────────────────────────────────────────────
    "net_value": ["net_value","vnt","valor neto","net_value_mm","vnt_mm"],
    "vei":       ["vei","value_efficiency_index","indice_eficiencia"],
    # ── Timing ────────────────────────────────────────────────────────────────
    "construction_years": [
        "construction_years","cy","años construccion","años_construccion",
        "anos construccion","años construcción","duracion","duración",
    ],
    "optimal_year": [
        "optimal_year","año optimo","año_optimo","optimal year",
        "año optimo ejecucion","año óptimo ejecución",
    ],
    "execution_year": [
        "execution_year","año ejecucion","año_ejecucion","execution year","vigencia",
    ],
    # ── Flags ─────────────────────────────────────────────────────────────────
    "is_mandatory": [
        "is_mandatory","obligatorio","mandatory","must_do","must-do",
        "obligatorio (s/n)",
    ],
    # ── Committed Spend ────────────────────────────────────────────
    "is_committed": [
        "is_committed","comprometido","committed","contrato_firmado",
        "comprometido (s/n)","con_contrato","ods_emitida","comprometido s/n",
    ],
    "committed_year": [
        "committed_year","año_compromiso","año compromiso","year_committed",
        "año contrato","anio_contrato","fecha_compromiso",
    ],
    # ── Riesgo ────────────────────────────────────────────────────────────────
    "cons_base": [
        "cons_base","consecuencia_base","consequence_base","consecuencia base",
        "consecuencia base l1-5","nivel consecuencia base","cons base",
    ],
    "prob_base": [
        "prob_base","probabilidad_base","probability_base","probabilidad base",
        "probabilidad base p1-5","nivel probabilidad base","prob base",
    ],
    "cons_res": [
        "cons_res","consecuencia_residual","consequence_residual",
        "consecuencia residual","cons residual",
    ],
    "prob_res": [
        "prob_res","probabilidad_residual","probability_residual",
        "probabilidad residual","prob residual",
    ],
    # ── Value Drivers ─────────────────────────────────────────────────────────
    "delta_ens": [
        "delta_ens","ens","mwh_año","mwh/año","ens_mwh",
        "ens evitada (mwh/año)","ens evitada","mwh año",
    ],
    "delta_saidi": [
        "delta_saidi","saidi","horas_cliente","horas-cliente/año",
        "saidi (horas-cli/año)","saidi horas cli",
    ],
    "delta_agua": [
        "delta_agua","m3_año","m3/año","agua_m3",
        "agua (m3/año)","continuidad agua",
    ],
    "delta_opex": [
        "delta_opex","opex_evitado","opex evitado","ahorro_opex",
        "opex evitado (mm/año)","opex evitado (mm ano)",
        "opex evitado (mm cop/año)","delta opex",
    ],
    "delta_conex": [
        "delta_conex","conexiones","nuevas conexiones","nuevas_conexiones",
        "nuevas conexiones t&d","conexiones t&d",
    ],
    # ── Dependencias ──────────────────────────────────────────────────────────
    "precedent_id": [
        "precedent_project_id","precedent_id","padre_id","padre",
        "parent_id","prerrequisito",
    ],
    "min_lag": ["min_lag_years","min_lag","lag_años","lag años"],
    # ── Asset ID para Must-Mitigate R8 ──────────────────────────────
    "asset_id": [
        "asset_id","activo_id","id_activo","activo","asset","codigo_activo",
        "equipo_id","id_equipo",
    ],
    # ── Exclusión mutua entre proyectos ─────────────────────────────
    "estimate_class": [
        "estimate_class","clase_estimativo","clase estimativo","base_estimativo",
        "base estimativo","clase_estimacion","clase estimacion","aace_class",
        "clase_aace","madurez_estimativo",
    ],
    "exclusion_group_id": [
        "exclusion_group_id","grupo_exclusion","grupo exclusion","exclusion_group",
        "grupo_alternativas","alternativas_grupo","excl_group",
    ],
    "mutual_exclusion_ids": [
        "mutual_exclusion_ids","excluye_a","excluye a","mutuamente_excluyentes",
        "mutex_ids","proyectos_excluyentes","excluyente_con",
    ],
    # ── Miscelánea ────────────────────────────────────────────────────────────
    "segment": ["segment","segmento","tipo_red"],
}

_BU_MAP = {
    "transmisión energía": "TyD", "transmision energia": "TyD",
    "distribución energía": "TyD", "distribucion energia": "TyD",
    "t&d": "TyD", "tyd": "TyD", "transmision": "TyD", "distribucion": "TyD",
    "acueducto": "AyA", "alcantarillado": "AyA", "aya": "AyA", "agua": "AyA",
    "acueducto y alcantarillado": "AyA",
    "gas": "Gas", "gas natural": "Gas",
    "generacion": "Generacion", "generación": "Generacion",
    "generation": "Generacion",
}

_INV_TYPE_MAP = {
    "reposición": "Reposición de Activos",  "reposicion": "Reposición de Activos",
    "reposición de activos": "Reposición de Activos",
    "crecimiento": "Crecimiento/Expansión", "expansion": "Crecimiento/Expansión",
    "crecimiento/expansión": "Crecimiento/Expansión",
    "cumplimiento": "Cumplimiento Regulatorio",
    "regulatorio": "Cumplimiento Regulatorio",
    "eficiencia": "Eficiencia Operativa",
    "esg": "ESG / Sostenibilidad",
    "ti": "Proyecto Empresarial/TI", "it": "Proyecto Empresarial/TI",
    "proyecto empresarial/ti": "Proyecto Empresarial/TI",
}


def _norm(s: str) -> str:
    """Normaliza string para comparación: minúsculas, sin tildes, strip."""
    s = s.lower().strip()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")]:
        s = s.replace(a, b)
    return s


def _map_columns(columns: List[str]) -> Dict[str, str]:
    """
    Construye mapa {campo_interno: nombre_columna_real}.
    Usa _COL_ALIASES para tolerancia a variaciones de nombre.
    """
    norm_cols = {_norm(c): c for c in columns}
    mapping: Dict[str, str] = {}
    for field_name, aliases in _COL_ALIASES.items():
        for alias in aliases:
            if _norm(alias) in norm_cols:
                mapping[field_name] = norm_cols[_norm(alias)]
                break
    return mapping


def _parse_cop(val: Any) -> float:
    """
    Convierte valor COP a float.
    Acepta: 1234567, "1.234.567", "1,234,567", "1234.56MM", 1234.56 (MM).
    Si el valor parece estar en MM COP (< 1e7), multiplica por 1e6.
    """
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return 0.0
    s = str(val).strip().replace(" ", "").upper()
    if not s or s in ("NAN", "NONE", "-", ""):
        return 0.0
    is_mm = "MM" in s
    s = s.replace("MM", "").replace("COP", "").replace("$", "")
    # Detectar si usa punto como separador de miles y coma como decimal
    if "," in s and "." in s:
        if s.index(".") < s.index(","):   # 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:                              # 1,234.56
            s = s.replace(",", "")
    elif "," in s:
        # ¿es decimal o miles? Si hay exactamente 3 dígitos después de la coma: miles
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) == 3:
            s = s.replace(",", "")        # 1,234 → 1234
        else:
            s = s.replace(",", ".")       # 1,5 → 1.5
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 2:               # 1.234.567
            s = s.replace(".", "")
    try:
        v = float(s)
    except ValueError:
        return 0.0
    if is_mm:
        return v * 1_000_000
    # Heurística: si el valor es < 1e7 y el campo espera COP, probablemente está en MM
    # Se resuelve en el loader con contexto de columna
    return v


def _detect_csv_separator(content: bytes) -> str:
    """Detecta separador CSV: coma, punto y coma, tabulador o pipe."""
    sample = content[:4096].decode("utf-8", errors="replace")
    counts = {",": sample.count(","), ";": sample.count(";"),
              "\t": sample.count("\t"), "|": sample.count("|")}
    return max(counts, key=counts.get)


class PortfolioLoader:
    """
    Carga un portafolio de proyectos desde Excel (.xlsx) o CSV y construye
    una lista de ProjectInput listos para el EnterpriseOptimizer.

    Características:
    - Detección automática de separador CSV (coma, punto y coma, tab, pipe)
    - Limpieza de nombres de columna (strip, normalización de tildes)
    - Mapeo tolerante de nombres de columna via _COL_ALIASES
    - Conversión inteligente de valores COP (MM COP ↔ COP)
    - Construcción automática de DriverInput desde columnas delta_*
    - Soporte de openpyxl para .xlsx con celdas fusionadas y filtros
    - Informe de columnas no mapeadas para que el analista las revise
    """

    YEARS = [2026, 2027, 2028, 2029, 2030]

    def __init__(self, config: dict = None):
        self.config = config or DEFAULT_CONFIG
        self._engine = ValueEngine(self.config)

    # ── Carga principal ───────────────────────────────────────────────────────

    def from_bytes(self, data: bytes, filename: str) -> Tuple[List[ProjectInput], dict]:
        """
        Carga portafolio desde bytes (para Streamlit st.file_uploader).

        Returns:
            projects: List[ProjectInput]
            report:   dict con {mapped, unmapped, warnings, n_rows, n_projects}
        """
        ext = Path(filename).suffix.lower()
        if ext in (".xlsx", ".xls"):
            return self._from_excel_bytes(data)
        else:
            return self._from_csv_bytes(data)

    def from_path(self, path: str) -> Tuple[List[ProjectInput], dict]:
        """Carga portafolio desde ruta de archivo."""
        with open(path, "rb") as f:
            return self.from_bytes(f.read(), path)

    # ── Excel ─────────────────────────────────────────────────────────────────

    def _from_excel_bytes(self, data: bytes) -> Tuple[List[ProjectInput], dict]:
        if not OPENPYXL_OK:
            raise ImportError("openpyxl no instalado. pip install openpyxl")
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return [], {"warnings": ["Archivo vacío"]}

        # Detectar la fila de encabezados real:
        # Es la primera fila con ≥ 3 celdas no vacías Y que al menos una
        # contiene una palabra clave reconocida por el mapeador de columnas.
        # Esto soporta archivos con título + subtítulo antes del header real.
        header_row_idx = 0
        for i, row in enumerate(rows[:20]):   # buscar en las primeras 20 filas
            non_empty = [str(c).strip() for c in row
                         if c is not None and str(c).strip() not in ("","None")]
            if len(non_empty) < 2:
                continue
            # Probar si este row produce al menos 2 columnas mapeadas
            trial_map = _map_columns(non_empty)
            if len(trial_map) >= 2:
                header_row_idx = i
                break
        else:
            # Fallback: primera fila con datos
            for i, row in enumerate(rows):
                if any(c is not None for c in row):
                    header_row_idx = i
                    break

        headers = [str(c).strip() if c is not None else ""
                   for c in rows[header_row_idx]]
        data_rows = []
        for row in rows[header_row_idx + 1:]:
            if any(c is not None and str(c).strip() not in ("", "None") for c in row):
                data_rows.append({headers[i]: row[i]
                                   for i in range(min(len(headers), len(row)))})
        return self._build_projects(data_rows, headers)

    # ── CSV ───────────────────────────────────────────────────────────────────

    def _from_csv_bytes(self, data: bytes) -> Tuple[List[ProjectInput], dict]:
        sep = _detect_csv_separator(data)
        text = data.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text), delimiter=sep)
        rows = [{k.strip(): v for k, v in row.items()} for row in reader]
        headers = list(rows[0].keys()) if rows else []
        # Filtrar filas totalmente vacías
        rows = [r for r in rows if any(str(v).strip() not in ("","None") for v in r.values())]
        return self._build_projects(rows, headers)

    # ── Constructor de ProjectInput ───────────────────────────────────────────

    def _build_projects(self, rows: List[dict], headers: List[str]
                        ) -> Tuple[List[ProjectInput], dict]:
        col_map = _map_columns(headers)
        mapped   = list(col_map.keys())
        unmapped = [h for h in headers if h not in col_map.values() and h]
        warnings = []
        projects = []

        for idx, row in enumerate(rows):
            g = lambda field: row.get(col_map.get(field, "___"), None)

            # ID y nombre
            raw_id = g("id") or f"P{idx+1:04d}"
            pid    = str(raw_id).strip()
            name   = str(g("name") or g("id") or pid).strip()

            # Skip filas de totales/subtotales
            if _norm(pid) in ("total","total general","subtotal","") or                _norm(name) in ("total","total general","subtotal"):
                continue

            # Business Unit
            bu_raw = str(g("business_unit") or g("segment") or "Todos").strip()
            bu = _BU_MAP.get(_norm(bu_raw), "Todos")

            # Tipo de inversión
            inv_raw = str(g("investment_type") or "Reposición de Activos").strip()
            inv_type = _INV_TYPE_MAP.get(_norm(inv_raw), inv_raw or "Reposición de Activos")

            # CAPEX por vigencia — detectar si está en MM o COP
            capex_by_year: Dict[int, float] = {}
            for yr in self.YEARS:
                raw = _parse_cop(g(f"capex_{yr}"))
                # Si todos los valores son < 1e7, están en MM COP
                capex_by_year[yr] = raw

            # Heurística: si la suma de CAPEX por año < 1000, están en MM
            total_yr = sum(capex_by_year.values())
            # Heurística: si la suma está entre 1 y 1.000.000, los valores están en MM COP
            # Cubre: portafolio TI (ej: 534.6 MM) y T&D (ej: 20949 MM)
            # No aplica si la suma es 0 (sin datos) o > 1M (ya en COP)
            in_mm = 0 < total_yr < 1_000_000  # valores típicos TI: 20-8000 MM | T&D: 500-300000 MM
            if in_mm:
                capex_by_year = {yr: v * 1_000_000 for yr, v in capex_by_year.items()}

            capex_total_raw = _parse_cop(g("capex_total"))
            if capex_total_raw > 0 and in_mm:
                capex_total_raw *= 1_000_000
            elif capex_total_raw > 0 and total_yr == 0 and 0 < capex_total_raw < 1_000_000:
                # Solo capex_total disponible (sin columnas capex_YYYY).
                # Aplica heurística MM independiente: cualquier valor < 1 MM COP
                # (≈ $250 USD) es irreal para un proyecto de infraestructura.
                capex_total_raw *= 1_000_000
            capex_total = capex_total_raw or sum(capex_by_year.values())

            if capex_total <= 0:
                # Intentar inferir CAPEX desde campos alternativos antes de omitir
                # 1. ¿Hay un campo "capex_total" directo?
                raw_ct = g("capex_total")
                if raw_ct:
                    ct = _parse_cop(raw_ct)
                    if ct > 0:
                        if 0 < ct < 1_000_000: ct *= 1_000_000  # en MM
                        capex_total = ct

            if capex_total <= 0:
                # Buscar si el CSV tenía una columna tipo capex_COP que no fue
                # mapeada (p.ej. columna llamada exactamente "CAPEX_COP" sin alias)
                capex_hint = ""
                raw_headers_lower = {h.lower().replace(" ", "_"): h
                                     for h in headers}
                for candidate in ("capex_cop", "capex", "inversion", "inversión",
                                  "costo", "cost"):
                    if candidate in raw_headers_lower:
                        capex_hint = (f" SUGERENCIA: columna '{raw_headers_lower[candidate]}' "
                                      f"encontrada — renómbrela a 'capex_total' o "
                                      f"'capex_2026' para que sea reconocida.")
                        break
                warnings.append(
                    f"⚠ PROYECTO OMITIDO — Fila {idx+2} ({pid}): CAPEX = 0 o no "
                    f"encontrado. Columnas CAPEX reconocidas: capex_2026..2030, "
                    f"capex_total, capex_cop. "
                    f"Columnas presentes: {headers[:10]}.{capex_hint}"
                )
                continue

            # ── Base del estimativo (incertidumbre propia de la alternativa) ──
            # Acepta "Class3", "3", "clase 3". Si no viene o no se reconoce, se
            # asume la clase más débil de config: un estimativo cuya madurez
            # nadie declaró no puede tratarse como si fuera firme.
            _unc_cfg   = DEFAULT_CONFIG.get("cost_estimate_uncertainty", {})
            _valid_cls = set((_unc_cfg.get("classes") or {}).keys())
            _raw_cls   = str(g("estimate_class") or "").strip()
            _norm_cls  = ""
            if _raw_cls:
                _digits = _re.search(r"[1-5]", _raw_cls)
                if _raw_cls in _valid_cls:
                    _norm_cls = _raw_cls
                elif _digits:
                    _cand = f"Class{_digits.group()}"
                    if _cand in _valid_cls:
                        _norm_cls = _cand
            if _raw_cls and not _norm_cls:
                warnings.append(
                    f"⚠ Fila {idx+2} ({pid}): base de estimativo '{_raw_cls}' no "
                    f"reconocida. Se asume {_unc_cfg.get('default_class','Class5')}. "
                    f"Valores válidos: Class1..Class5 o 1..5."
                )

            costs = CostInput(
                capex_COP=capex_total,
                estimate_class=_norm_cls or _unc_cfg.get("default_class", "Class5"),
                capex_by_year={yr: v for yr, v in capex_by_year.items() if v > 0}
            )

            # Años de timing
            def _yr(field, default=2026):
                v = g(field)
                try: return int(float(str(v))) if v else default
                except: return default

            exec_yr = _yr("execution_year", 2026)
            opt_yr  = _yr("optimal_year",   exec_yr)
            cy      = max(1, _yr("construction_years", 1))

            # Mandatory
            mand_raw = str(g("is_mandatory") or "False").strip().lower()
            is_mand  = mand_raw in ("true","1","yes","sí","si","verdadero","x")

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

            # Drivers desde columnas delta_*
            drivers = self._build_drivers(g, bu)

            # Si no hay drivers, crear uno vacío para que el motor no falle
            if not drivers:
                warnings.append(f"{pid}: sin drivers de valor — VNT = solo riesgo")

            # Precedencias
            prec_id = str(g("precedent_id") or "").strip()
            min_lag = 0
            try: min_lag = int(float(str(g("min_lag") or 0)))
            except: pass

            # Asset_id para restricción Must-Mitigate R8
            asset_id_raw = str(g("asset_id") or "").strip()

            # Exclusion_group_id y mutual_exclusion_ids
            excl_grp = str(g("exclusion_group_id") or "").strip()
            mutex_raw = str(g("mutual_exclusion_ids") or "").strip()
            mutex_list = [s.strip() for s in mutex_raw.split(",") if s.strip()] if mutex_raw else []

            # Committed Spend (ingresado por el analista)
            committed_raw = str(g("is_committed") or "False").strip().lower()
            is_committed_flag = committed_raw in ("true","1","yes","sí","si","verdadero","x")
            committed_yr = _yr("committed_year", 0)

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
            proj.precedent_project_id = prec_id
            proj.min_lag_years = min_lag
            proj.mutual_exclusion_ids = mutex_list
            proj.exclusion_group_id   = excl_grp or None
            proj.asset_id             = asset_id_raw
            projects.append(proj)

        # ── Deduplicar IDs ────────────────────────────────────────────────────
        # El sufijo indica el número de ocurrencia: la primera queda sin sufijo
        # (es la "_01" implícita) y la segunda pasa a ser '_02'. Así el nombre
        # se lee como "la segunda P001" en lugar de "duplicado nº 1".
        #
        # El ID generado se registra en el mismo índice: si el archivo ya trae
        # un 'P001_02' real, el renombrado salta al siguiente libre en vez de
        # crear la colisión que venía a evitar.
        seen_ids: Dict[str, int] = {}
        taken: set = set()
        for p in projects:
            if p.id in seen_ids or p.id in taken:
                base = p.id
                n = max(seen_ids.get(base, 1), 1)
                while True:
                    n += 1
                    candidate = f"{base}_{n:02d}"
                    if candidate not in taken and candidate not in seen_ids:
                        break
                seen_ids[base] = n
                p.id = candidate
                warnings.append(
                    f"ID duplicado '{base}' renombrado a '{p.id}'"
                )
            else:
                seen_ids[p.id] = 1
            taken.add(p.id)

        report = {
            "mapped":     mapped,
            "unmapped":   unmapped,
            "warnings":   warnings,
            "n_rows":     len(rows),
            "n_projects": len(projects),
        }
        return projects, report

    def _build_drivers(self, g, bu: str) -> List[DriverInput]:
        """
        Construye DriverInput desde columnas delta_* del Excel.
        Crea un driver por reto con los sub-criterios que encuentre.
        """
        cfg_vds = self.config.get("value_drivers", [])
        sc_prices: Dict[str, float] = {}
        sc_units:  Dict[str, str]   = {}
        for vd in cfg_vds:
            for sc in vd.get("sub_criteria", []):
                sc_prices[sc["id"]] = sc.get("price_per_unit_COP", 1.0)
                sc_units[sc["id"]]  = sc.get("unit", "")

        # Mapa de columna → (sc_id, precio)
        col_sc = {
            "delta_ens":   ("VD-1-A", sc_prices.get("VD-1-A", 335_000)),
            "delta_saidi": ("VD-1-B", sc_prices.get("VD-1-B", 6_200)),
            "delta_agua":  ("VD-1-C", sc_prices.get("VD-1-C", 3_100)),
            "delta_opex":  ("VD-2-A", sc_prices.get("VD-2-A", 1.0)),
        }

        # Agrupar sub-criterios por VD
        vd_scs: Dict[str, List[SubCriterionInput]] = {}
        vd_retos = {
            "VD-1": "Calidad de los Servicios",
            "VD-2": "Servicios Eficientes",
            "VD-3": "Cobertura Universal Sostenible",
            "VD-4": "Protección Hídrica y Carbono Neutralidad",
            "VD-5": "Generación de Valor",
        }
        for col_key, (sc_id, price) in col_sc.items():
            raw = _parse_cop(g(col_key))
            if raw and raw > 0:
                vd_id = sc_id[:4]
                sc = SubCriterionInput(
                    id=sc_id, label=sc_id,
                    type="monetizable",
                    delta_annual=raw,
                    price_per_unit=price,
                )
                vd_scs.setdefault(vd_id, []).append(sc)

        # "delta_conex" (nuevas conexiones) es VD-3, pero el sub-criterio y su
        # precio dependen del negocio (VD-3-A T&D, VD-3-B AyA, VD-3-C Gas) —
        # no puede resolverse con una entrada fija en col_sc como las demás.
        _conex_sc_by_bu = {"TyD": "VD-3-A", "AyA": "VD-3-B", "Gas": "VD-3-C"}
        _conex_sc_id = _conex_sc_by_bu.get(bu)
        if _conex_sc_id:
            raw_conex = _parse_cop(g("delta_conex"))
            if raw_conex and raw_conex > 0:
                sc = SubCriterionInput(
                    id=_conex_sc_id, label=_conex_sc_id,
                    type="monetizable",
                    delta_annual=raw_conex,
                    price_per_unit=sc_prices.get(_conex_sc_id, 5_200_000),
                )
                vd_scs.setdefault("VD-3", []).append(sc)

        drivers = []
        for vd_id, scs in vd_scs.items():
            drivers.append(DriverInput(
                id=vd_id,
                reto=vd_retos.get(vd_id, vd_id),
                sub_criteria=scs,
            ))
        return drivers

    def enrich_with_vnt(self, projects: List[ProjectInput]) -> List[dict]:
        """
        Evalúa cada proyecto con el ValueEngine y devuelve lista de dicts
        con campos enriquecidos: net_value_COP, vei, model_level, urgency.
        """
        results = []
        for p in projects:
            try:
                r = self._engine.evaluate(p)
                results.append({
                    "project_id":   p.id,
                    "project_name": p.name,
                    "business_unit":p.business_unit,
                    "investment_type": p.investment_type,
                    "capex_COP":    p.costs.capex_COP,
                    "net_value_COP":r["net_value_COP"],
                    "vei":          r["value_efficiency_index"],
                    "model_level":  r.get("model_level", "RULE_BASED"),
                    "optimal_year": r.get("optimal_year", p.optimal_year),
                    "is_mandatory": p.is_mandatory,
                    "vd_breakdown": {
                        dr["id"]: dr["value_COP"]
                        for dr in r.get("driver_results", [])
                    },
                })
            except Exception as e:
                results.append({
                    "project_id": p.id, "project_name": p.name,
                    "error": str(e), "net_value_COP": 0, "vei": 0,
                })
        return results
