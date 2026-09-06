"""
app_epm_aip_full_enterprise.py
═══════════════════════════════════════════════════════════════════
EPM Medellín — Motor Predictivo AIP Enterprise
Interfaz Streamlit de grado industrial

Requiere: epm_core.py en el mismo directorio
Ejecutar: streamlit run app_epm_aip_full_enterprise.py

Pestañas:
  1. 🏠 Dashboard   — KPIs estratégicos, Treemap de valor, consola ejecutiva
  2. 🔬 Evaluación  — Wizard Duelo de Alternativas, Weibull interactivo, VNT/VEI
  3. 📊 Planeador   — Portafolio, MILP Enterprise, Frontera Eficiente, Versionado PAI
  4. 📋 Auditoría   — Snapshots ISO 55001, Degradación, Capacity Gap, Scenario Duel,
                     EVM, Outage-Aligned Scheduling, Workflow de Aprobación
  5. ⚙️  Config     — WACC, shadow prices, umbrales, tasas de degradación, outages
"""

import streamlit as st
import json, math, copy, hashlib, io, traceback
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import plotly.io as pio
from plotly.subplots import make_subplots

# Compatibilidad pandas: applymap fue renombrado a map en pandas >= 2.1
_PD_VERSION = tuple(int(x) for x in pd.__version__.split(".")[:2])
_STYLER_MAP = "map" if _PD_VERSION >= (2, 1) else "applymap"

def _style_apply(styler, func, subset=None):
    """Aplica función de estilo compatible con pandas < 2.1 y >= 2.1."""
    if _PD_VERSION >= (2, 1):
        return styler.map(func, subset=subset)
    return styler.applymap(func, subset=subset)

# ── Core engine ───────────────────────────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).parent))

from epm_core import (
    ValueEngine, EnterpriseOptimizer, AssetDegradationEngine,
    SnapshotStore, DependencyEngine, DependencyError, PortfolioLoader,
    ResourcePeriod, EnterprisePortfolioResult,
    AssetProfile, DegradationModel, DegradationResult,
    ProjectInput, DriverInput, SubCriterionInput, CostInput, RiskInput,
    BudgetPeriod, PortfolioOptimizer, SensitivityAnalyst,
    DEFAULT_CONFIG, INVESTMENT_TYPES, BU_DRIVER_CONFIG, ASSET_FAMILIES,
    load_config, save_config, _merge_config_defaults, apply_bu_driver_config,
    PULP_OK, SCIPY_OK,
    # ── Brecha J: Earned Value Management ────────────────────────────────────
    ProjectActual, EVMResult, EVMEngine, ActualsLoader,
    # ── Brecha L: Outage-Aligned Scheduling ──────────────────────────────────
    PlannedOutage, OutageCalendar,
    # ── Brecha E: Workflow de Aprobación ─────────────────────────────────────
    WF_STATES, WF_STATE_COLOR, WF_FULL_TRANSITIONS, WF_TERMINAL_STATES,
    WorkflowTransition, ProjectWorkflow, WorkflowEngine,
    # ── Incertidumbre de estimación del CAPEX (por alternativa) ───────────────
    SensitivityScenario, SensitivityResult,
    _scale_project_for_estimate,
    # ── Brecha H: Committed Spend + Plan Versioning ───────────────────────────
    PlanVersion, PlanVersionStore,
    EstimateAccuracyStore,
    # ── D-14: Registro de Activos ─────────────────────────────────────────────
    AssetRegistryStore,
)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN PÁGINA
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="EPM · Motor AIP Enterprise",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",    # rail siempre visible → layout predecible, sin ghost margin
)

# ══════════════════════════════════════════════════════════════════════════════
# SISTEMA DE DISEÑO — Identidad EPM + Patrones Copperleaf C55
# ══════════════════════════════════════════════════════════════════════════════
# --- UI/UX DECISION --- Paleta EPM v15.1: verde cítrico #00A86B como primario oficial
EPM_GREEN   = "#00A86B"   # Verde cítrico primario EPM (identidad corporativa)
EPM_DARK    = "#007A3E"   # Verde bosque secundario
EPM_DEEP    = "#005C2E"   # Verde oscuro (headers, sidebar activo)
EPM_DEEPER  = "#003D1E"   # Verde muy oscuro (sidebar bg)
EPM_LIGHT   = "#E6F9F1"   # Verde cítrico claro (fondos de cards)
EPM_MID     = "#B3EFD6"   # Verde cítrico medio (bordes suaves)
EPM_AMBER   = "#D97706"   # Ámbar (advertencias, CoD)
EPM_RED     = "#DC2626"   # Rojo (VNT negativo, urgencia)
EPM_BLUE    = "#1E40AF"   # Azul (info, N2 métrico)
EPM_TEAL    = "#0F766E"   # Teal (degradación, N3 predictivo)
EPM_SLATE   = "#1E293B"   # Slate oscuro (texto principal)
EPM_GRAY    = "#94A3B8"   # Gris (texto secundario, slate-400 — cumple WCAG AA en fondos oscuros)
EPM_BORDER  = "#E2E8F0"   # Borde tablas y cards

# --- UI/UX DECISION --- Colores Plotly alineados con paleta actualizada
PLOTLY_COLORS = [
    "#00A86B", "#007A3E", "#0F766E", "#1E40AF", "#D97706",
    "#005C2E", "#0369A1", "#7C3AED", "#BE185D", "#B45309",
]

# Template Plotly oscuro global — aplicar en update_layout de cada figura
_DARK_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(15,23,42,0.60)",
    font=dict(color="#CBD5E1", family="Inter, sans-serif"),
    xaxis=dict(
        gridcolor="rgba(255,255,255,0.06)",
        zerolinecolor="rgba(255,255,255,0.12)",
        tickfont=dict(color="#94A3B8"),
        title_font=dict(color="#94A3B8"),
    ),
    yaxis=dict(
        gridcolor="rgba(255,255,255,0.06)",
        zerolinecolor="rgba(255,255,255,0.12)",
        tickfont=dict(color="#94A3B8"),
        title_font=dict(color="#94A3B8"),
    ),
    legend=dict(
        bgcolor="rgba(15,23,42,0.55)",
        bordercolor="rgba(255,255,255,0.10)",
        borderwidth=1,
        font=dict(color="#CBD5E1"),
    ),
)

# Registrar y activar template oscuro como default global de Plotly
_epm_dark = go.layout.Template(layout=go.Layout(**_DARK_LAYOUT))
pio.templates["epm_dark"] = _epm_dark
pio.templates.default = "plotly+epm_dark"

# ── Utilidad v15.0: normalización de nombres de BU (sin tildes, case-insensitive) ──
import unicodedata as _ud

def normalize_bu(s: str) -> str:
    """Normaliza un nombre de BU: quita tildes y convierte a minúsculas.
    Ej: 'Generación' → 'generacion', 'T&D' → 't&d'
    """
    return "".join(
        c for c in _ud.normalize("NFD", s.lower())
        if _ud.category(c) != "Mn"
    )

# Mapa canónico: variantes de BU → clave normalizada usada en config
_BU_NORM_MAP = {
    normalize_bu(k): k
    for k in ["TyD", "AyA", "Gas", "Generacion", "TI", "Todos"]
}

CSS = """
<style>
/* ════════════════════════════════════════════════════════════════════════════
   EPM AIP Enterprise — Design System v15.1
   Identidad corporativa EPM · Inter · Verde cítrico #00A86B
   ════════════════════════════════════════════════════════════════════════════ */

/* --- UI/UX DECISION --- Inter: tipografía humanista moderna, alta legibilidad
   en interfaces enterprise. Alternativa a IBM Plex Sans solicitada por el cliente. */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

/* ════════════════════════════════════════════════════════════════════════════
   1. BASE — Reset, fuente, fondo
   ════════════════════════════════════════════════════════════════════════════ */

/* --- UI/UX DECISION --- Reset limpio + Inter como sistema de fuente unificado */
*, *::before, *::after { box-sizing: border-box; }
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}

/* ── LAYOUT CRÍTICO ────────────────────────────────────────────────────────────
   Streamlit wide mode: el sidebar maneja su propio ancho via JS.
   No tocamos sidebar width — solo padding del contenido principal.
   ──────────────────────────────────────────────────────────────────────────── */
section[data-testid="stMain"] > div { padding-top: 0 !important; }
.main .block-container {
    padding: 1rem 2.5rem 3rem 2.5rem !important;
    max-width: 100% !important;
    width: 100% !important;
}

/* ════════════════════════════════════════════════════════════════════════════
   2. TIPOGRAFÍA — Jerarquía clara
   ════════════════════════════════════════════════════════════════════════════ */

/* --- UI/UX DECISION --- Headings con Inter: más grande, más peso, mejor jerarquía */
h1 {
    font-size: 1.35rem !important; font-weight: 700 !important;
    color: #F0FDF4 !important; font-family: 'Inter', sans-serif !important;
    letter-spacing: -0.02em !important; line-height: 1.3 !important;
}
h2 {
    font-size: 1.1rem !important; font-weight: 600 !important;
    color: #E2E8F0 !important; font-family: 'Inter', sans-serif !important;
    letter-spacing: -0.01em !important;
}
h3 {
    font-size: 0.95rem !important; font-weight: 600 !important;
    color: #CBD5E1 !important; font-family: 'Inter', sans-serif !important;
}
p, li, span {
    font-size: 0.9rem;
    line-height: 1.6;
}

/* ════════════════════════════════════════════════════════════════════════════
   3. HEADER DE PÁGINA
   ════════════════════════════════════════════════════════════════════════════ */

/* Page header: dark premium — borde izquierdo verde, fondo glass oscuro */
.epm-page-header {
    background: rgba(15,23,42,0.85);
    border-left: 4px solid #00A86B;
    border-radius: 0 10px 10px 0;
    padding: 16px 24px 14px 24px;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    backdrop-filter: blur(12px);
    border-top: 1px solid rgba(0,168,107,0.2);
    border-right: 1px solid rgba(255,255,255,0.06);
    border-bottom: 1px solid rgba(255,255,255,0.06);
    box-shadow: 0 2px 12px rgba(0,0,0,0.4);
}
.epm-page-header .page-title {
    font-size: 1.3rem;
    font-weight: 700;
    color: #FFFFFF;
    letter-spacing: -0.02em;
    margin: 0;
    line-height: 1.2;
    font-family: 'Inter', sans-serif;
}
.epm-page-header .page-sub {
    font-size: 0.78rem;
    color: #A7F3D0;
    margin-top: 4px;
    font-weight: 400;
    opacity: 0.92;
}
.epm-page-header .page-badge {
    background: rgba(255, 255, 255, 0.18);
    border: 1px solid rgba(255, 255, 255, 0.3);
    border-radius: 20px;
    padding: 5px 14px;
    font-size: 0.74rem;
    color: #ECFDF5;
    font-weight: 500;
    white-space: nowrap;
    letter-spacing: 0.02em;
}

/* ════════════════════════════════════════════════════════════════════════════
   4. SIDEBAR — Copperleaf persistence rail: brand + upload + status only
   ════════════════════════════════════════════════════════════════════════════ */

/* --- UI/UX DECISION --- Rail oscuro EPM. Ancho reducido (260px) para maximizar
   el área de trabajo. Solo logo, carga de archivo y estado del sistema.
   Todos los controles funcionales viven en el área de contenido (Copperleaf C55). */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #002B15 0%, #003D1E 100%) !important;
    border-right: 1px solid rgba(0, 168, 107, 0.20);
}
/* Quitar el padding extra del contenido del sidebar */
[data-testid="stSidebar"] > div:first-child {
    padding: 0 !important;
}
[data-testid="stSidebar"] > div:first-child { padding-top: 0 !important; }
[data-testid="stSidebar"] .stMarkdown,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label   { color: #D1FAE5 !important; }
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3      { color: #ECFDF5 !important; }
[data-testid="stSidebar"] hr      { border-color: rgba(0,168,107,0.18) !important; margin: 12px 0 !important; }
[data-testid="stSidebar"] .stCaption { color: #6EE7B7 !important; font-size: 0.7rem !important; }
[data-testid="stSidebar"] .stButton > button {
    background: rgba(0, 168, 107, 0.22) !important;
    color: #ECFDF5 !important;
    border: 1px solid rgba(0, 168, 107, 0.45) !important;
    border-radius: 6px !important;
    font-weight: 600 !important;
    font-size: 0.82rem !important;
    padding: 8px 14px !important;
    width: 100%;
    transition: all 0.15s ease;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: rgba(0, 168, 107, 0.40) !important;
    border-color: rgba(0, 168, 107, 0.7) !important;
}
[data-testid="stSidebar"] [data-testid="stFileUploader"] {
    background: rgba(0, 168, 107, 0.10);
    border: 1.5px dashed rgba(0, 168, 107, 0.45);
    border-radius: 8px;
    padding: 8px;
}

/* LOGO sidebar — marca corporativa EPM */
.epm-sidebar-logo {
    background: linear-gradient(180deg, #001A0D 0%, #002B15 100%);
    padding: 20px 16px 16px 16px;
    border-bottom: 1px solid rgba(0, 168, 107, 0.25);
    margin-bottom: 12px;
}
.epm-sidebar-logo .logo-bolt {
    font-size: 1.6rem;
    line-height: 1;
    margin-bottom: 2px;
}
.epm-sidebar-logo .logo-main {
    font-size: 1.45rem;
    font-weight: 800;
    color: #FFFFFF;
    letter-spacing: 0.06em;
    font-family: 'Inter', sans-serif;
    line-height: 1;
}
.epm-sidebar-logo .logo-sub {
    font-size: 0.58rem;
    color: #6EE7B7;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    margin-top: 5px;
    font-weight: 600;
}
.epm-sidebar-logo .logo-version {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(0, 168, 107, 0.20);
    border: 1px solid rgba(0, 168, 107, 0.40);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 0.60rem;
    color: #A7F3D0;
    margin-top: 10px;
    font-family: 'JetBrains Mono', monospace;
    letter-spacing: 0.04em;
}
/* Pill de estado de motor en la versión badge */
.epm-sidebar-logo .logo-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: #00A86B; display: inline-block;
    box-shadow: 0 0 5px rgba(0,168,107,0.8);
}

/* Sección dentro del rail */
.rail-section-label {
    font-size: 0.70rem !important;
    font-weight: 700 !important;
    color: #6EE7B7 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.10em !important;
    margin: 0 0 6px 0 !important;
    display: block;
}

/* Portfolio status card */
.rail-portfolio-card {
    background: rgba(0, 122, 62, 0.18);
    border: 1px solid rgba(0, 168, 107, 0.35);
    border-radius: 7px;
    padding: 10px 12px;
    margin: 8px 0;
}
.rail-portfolio-card .rpc-title {
    font-size: 0.65rem;
    color: #6EE7B7;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.rail-portfolio-card .rpc-count {
    font-size: 1.05rem;
    font-weight: 800;
    color: #ECFDF5;
    margin-top: 3px;
    font-family: 'Inter', sans-serif;
}
.rail-portfolio-card .rpc-meta {
    font-size: 0.66rem;
    color: #A7F3D0;
    margin-top: 2px;
    line-height: 1.6;
}

/* System status row */
.rail-sys-status {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.68rem;
    color: #A7F3D0;
    padding: 3px 0;
    font-family: 'Inter', sans-serif;
}
.rail-sys-dot {
    width: 7px; height: 7px; border-radius: 50%;
    flex-shrink: 0;
}
.rail-sys-dot.ok  { background: #00A86B; box-shadow: 0 0 4px rgba(0,168,107,0.7); }
.rail-sys-dot.err { background: #EF4444; }
.rail-sys-dot.warn { background: #F59E0B; }

/* ════════════════════════════════════════════════════════════════════════════
   5. TABS — Flat, full-width, zero island effect (Copperleaf C55 pattern)
   ════════════════════════════════════════════════════════════════════════════ */

/* Tab bar: dark glass — borde inferior verde */
.stTabs [data-baseweb="tab-list"] {
    background: rgba(15,23,42,0.75);
    border-bottom: 2px solid rgba(0,168,107,0.25);
    border-radius: 0;
    padding: 0 8px;
    gap: 0;
    box-shadow: none;
    margin: 0;
    backdrop-filter: blur(8px);
}
.stTabs [data-baseweb="tab"] {
    font-size: 0.875rem !important;
    font-weight: 500;
    color: #94A3B8 !important;
    padding: 12px 20px !important;
    border-bottom: 3px solid transparent !important;
    border-radius: 0 !important;
    background: transparent !important;
    transition: color 0.15s, border-color 0.15s;
    font-family: 'Inter', sans-serif !important;
    letter-spacing: -0.01em;
    white-space: nowrap;
}
.stTabs [data-baseweb="tab"]:hover {
    color: #6EE7B7 !important;
    background: rgba(0,168,107,0.08) !important;
}
.stTabs [aria-selected="true"] {
    color: #A7F3D0 !important;
    border-bottom-color: #00A86B !important;
    font-weight: 700 !important;
    background: rgba(0,168,107,0.10) !important;
}
/* Tab panel: dark glass, sin borde */
.stTabs [data-baseweb="tab-panel"] {
    background: rgba(15,23,42,0.55);
    border-radius: 0;
    padding: 20px 20px 28px 20px !important;
    border: none !important;
    box-shadow: none !important;
    margin: 0;
}

/* ════════════════════════════════════════════════════════════════════════════
   5b. ESPACIADO DE CONTENIDO — Respiración entre elementos
   ════════════════════════════════════════════════════════════════════════════ */

/* Cada bloque de widget tiene margen inferior para que no queden apretados */
[data-testid="stVerticalBlock"] > [data-testid="stVerticalBlock"],
.stForm .stVerticalBlock > div { margin-bottom: 0.5rem !important; }

/* Separación entre columnas de formulario */
[data-testid="stHorizontalBlock"] { gap: 1.5rem !important; }

/* Radio group: más aire entre opciones */
.stRadio [data-testid="stWidgetLabel"] { margin-bottom: 8px !important; }
.stRadio > div { gap: 14px !important; }

/* Secciones dentro del tab-panel: separación entre bloques temáticos */
.stTabs [data-baseweb="tab-panel"] > div > div > div { margin-bottom: 1rem; }

/* ════════════════════════════════════════════════════════════════════════════
   6. WIDGETS — EL PROBLEMA PRINCIPAL: DROPDOWNS Y LABELS
   ════════════════════════════════════════════════════════════════════════════ */

/* --- UI/UX DECISION --- Labels: 0.875rem con peso 600. Antes eran ~0.75rem
   (default Streamlit) — ilegibles en pantallas enterprise */
[data-testid="stWidgetLabel"],
label[data-testid="stWidgetLabel"] p,
.stSelectbox label,
.stTextInput label,
.stNumberInput label,
.stTextArea label,
.stRadio label,
.stCheckbox label,
.stSlider label,
.stMultiSelect label {
    font-size: 0.875rem !important;
    font-weight: 600 !important;
    color: #CBD5E1 !important;
    margin-bottom: 5px !important;
    letter-spacing: -0.01em !important;
    font-family: 'Inter', sans-serif !important;
}

/* --- UI/UX DECISION --- Selectbox: FIX PRINCIPAL
   Antes: 0.82rem sin min-height → texto cortado, dropdown incómodo
   Ahora: 0.95rem, min-height 46px, padding generoso, texto sin truncar */
[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
[data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover {
    min-height: 46px !important;
    border-radius: 6px !important;
    border-color: rgba(255,255,255,0.15) !important;
    background: rgba(30,41,59,0.85) !important;
    font-size: 0.95rem !important;
    font-family: 'Inter', sans-serif !important;
    cursor: pointer !important;
    transition: border-color 0.15s, box-shadow 0.15s !important;
}
[data-testid="stSelectbox"] div[data-baseweb="select"] > div:focus-within {
    border-color: #00A86B !important;
    box-shadow: 0 0 0 3px rgba(0, 168, 107, 0.20) !important;
}
/* Texto seleccionado en el selectbox */
[data-testid="stSelectbox"] div[data-baseweb="select"] span,
[data-testid="stSelectbox"] div[data-baseweb="select"] [data-testid="stMarkdownContainer"] p {
    font-size: 0.95rem !important;
    color: #E2E8F0 !important;
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: unset !important;
    line-height: 1.4 !important;
    font-family: 'Inter', sans-serif !important;
}
/* Opciones del dropdown: dark glass */
[data-baseweb="menu"] li,
[data-baseweb="menu"] [role="option"],
[data-baseweb="popover"] [role="option"] {
    font-size: 0.92rem !important;
    font-family: 'Inter', sans-serif !important;
    padding: 11px 16px !important;
    min-height: 40px !important;
    display: flex !important;
    align-items: center !important;
    color: #E2E8F0 !important;
    line-height: 1.4 !important;
    cursor: pointer !important;
    transition: background 0.1s ease !important;
}
[data-baseweb="menu"] [role="option"]:hover,
[data-baseweb="menu"] li:hover {
    background: rgba(0,168,107,0.15) !important;
    color: #A7F3D0 !important;
}
[data-baseweb="menu"] [aria-selected="true"] {
    background: rgba(0,168,107,0.22) !important;
    color: #6EE7B7 !important;
    font-weight: 600 !important;
}
/* Contenedor del dropdown */
[data-baseweb="menu"],
[data-baseweb="popover"] {
    border-radius: 8px !important;
    border: 1px solid rgba(255,255,255,0.10) !important;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5) !important;
    background: #1E293B !important;
    overflow: hidden !important;
}

/* --- UI/UX DECISION --- Text inputs: altura 46px, fuente 0.95rem */
.stTextInput > div > div > input,
.stNumberInput > div > div > input {
    border-radius: 6px !important;
    border-color: rgba(255,255,255,0.15) !important;
    background: rgba(30,41,59,0.85) !important;
    font-size: 0.95rem !important;
    font-family: 'Inter', sans-serif !important;
    min-height: 46px !important;
    padding: 10px 14px !important;
    color: #E2E8F0 !important;
    transition: border-color 0.15s, box-shadow 0.15s !important;
}
.stTextInput > div > div > input:focus,
.stNumberInput > div > div > input:focus {
    border-color: #00A86B !important;
    box-shadow: 0 0 0 3px rgba(0, 168, 107, 0.20) !important;
    outline: none !important;
}

.stTextArea > div > div > textarea {
    border-radius: 6px !important;
    border-color: rgba(255,255,255,0.15) !important;
    background: rgba(30,41,59,0.85) !important;
    font-size: 0.93rem !important;
    font-family: 'Inter', sans-serif !important;
    line-height: 1.55 !important;
    padding: 10px 14px !important;
    color: #E2E8F0 !important;
    transition: border-color 0.15s, box-shadow 0.15s !important;
}
.stTextArea > div > div > textarea:focus {
    border-color: #00A86B !important;
    box-shadow: 0 0 0 3px rgba(0, 168, 107, 0.20) !important;
    outline: none !important;
}

[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
    min-height: 46px !important;
    border-radius: 6px !important;
    border-color: rgba(255,255,255,0.15) !important;
    background: rgba(30,41,59,0.85) !important;
    font-size: 0.92rem !important;
    font-family: 'Inter', sans-serif !important;
}
[data-testid="stMultiSelect"] [data-baseweb="tag"] {
    background: rgba(0,168,107,0.25) !important;
    border: 1px solid rgba(0,168,107,0.45) !important;
    border-radius: 4px !important;
    font-size: 0.82rem !important;
    color: #A7F3D0 !important;
    font-weight: 500 !important;
}

.stRadio > div {
    gap: 10px !important;
}
.stRadio > div > label {
    font-size: 0.92rem !important;
    font-weight: 400 !important;
    color: #CBD5E1 !important;
    padding: 6px 4px !important;
    cursor: pointer !important;
    transition: color 0.1s !important;
}
.stRadio > div > label:hover { color: #6EE7B7 !important; }
[data-testid="stRadio"] [data-baseweb="radio"] [data-checked="true"] + span {
    color: #A7F3D0 !important;
    font-weight: 500 !important;
}

/* --- UI/UX DECISION --- Slider: thumb verde cítrico, track más visible */
.stSlider [data-baseweb="slider"] [role="slider"] {
    background: #00A86B !important;
    border: 2px solid #FFFFFF !important;
    box-shadow: 0 1px 4px rgba(0, 168, 107, 0.4) !important;
    width: 18px !important;
    height: 18px !important;
}
.stSlider [data-baseweb="slider"] [data-testid="stThumbValue"] {
    font-size: 0.78rem !important;
    background: #00A86B !important;
    color: white !important;
    border-radius: 4px !important;
    padding: 2px 6px !important;
}
.stSlider [role="progressbar"] {
    background: #00A86B !important;
}

/* ════════════════════════════════════════════════════════════════════════════
   7. BOTONES
   ════════════════════════════════════════════════════════════════════════════ */

/* --- UI/UX DECISION --- Botón primario: verde cítrico, padding cómodo,
   fuente 0.92rem, sombra con color de marca */
.stButton > button[kind="primary"],
button[kind="primary"] {
    background: linear-gradient(135deg, #007A3E 0%, #00A86B 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 6px !important;
    font-weight: 600 !important;
    font-size: 0.92rem !important;
    padding: 10px 24px !important;
    min-height: 42px !important;
    box-shadow: 0 2px 8px rgba(0, 168, 107, 0.3) !important;
    transition: all 0.18s ease !important;
    font-family: 'Inter', sans-serif !important;
    letter-spacing: -0.01em !important;
}
.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #005C2E 0%, #007A3E 100%) !important;
    box-shadow: 0 4px 14px rgba(0, 168, 107, 0.4) !important;
    transform: translateY(-1px) !important;
}
.stButton > button[kind="primary"]:active {
    transform: translateY(0) !important;
    box-shadow: 0 1px 4px rgba(0, 168, 107, 0.3) !important;
}

/* Botón secundario */
.stButton > button:not([kind="primary"]) {
    border: 1.5px solid rgba(255,255,255,0.18) !important;
    border-radius: 6px !important;
    font-size: 0.9rem !important;
    font-weight: 500 !important;
    color: #CBD5E1 !important;
    background: rgba(30,41,59,0.75) !important;
    min-height: 42px !important;
    padding: 9px 20px !important;
    transition: all 0.15s ease !important;
    font-family: 'Inter', sans-serif !important;
}
.stButton > button:not([kind="primary"]):hover {
    border-color: rgba(0,168,107,0.55) !important;
    color: #A7F3D0 !important;
    background: rgba(0,168,107,0.12) !important;
    box-shadow: 0 2px 8px rgba(0, 168, 107, 0.20) !important;
}

/* ════════════════════════════════════════════════════════════════════════════
   8. KPI CARDS — Dashboard tiles
   ════════════════════════════════════════════════════════════════════════════ */

/* --- UI/UX DECISION --- Cards más grandes, borde superior más grueso (4px),
   valor principal más grande (1.75rem), mejor respiración vertical */
.kpi-strip {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 14px;
    margin-bottom: 24px;
}
.kpi-card {
    background: rgba(30,41,59,0.72);
    border: 1px solid rgba(255,255,255,0.08);
    border-top: 4px solid #00A86B;
    border-radius: 8px;
    padding: 18px 20px 16px 20px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.35);
    backdrop-filter: blur(8px);
    transition: box-shadow 0.2s ease, transform 0.2s ease;
}
.kpi-card:hover {
    box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    transform: translateY(-1px);
    border-color: rgba(0,168,107,0.25);
}
.kpi-card.amber { border-top-color: #D97706; }
.kpi-card.red   { border-top-color: #DC2626; }
.kpi-card.teal  { border-top-color: #0F766E; }
.kpi-card.blue  { border-top-color: #1E40AF; }
.kpi-card.gray  { border-top-color: #94A3B8; }
.kpi-label {
    font-size: 0.7rem;
    font-weight: 600;
    color: #94A3B8;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 8px;
    font-family: 'Inter', sans-serif;
}
.kpi-value {
    font-size: 1.75rem;
    font-weight: 700;
    color: #F0FDF4;
    line-height: 1.1;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.03em;
    font-family: 'Inter', sans-serif;
}
.kpi-sub {
    font-size: 0.72rem;
    color: #94A3B8;
    margin-top: 5px;
    font-family: 'Inter', sans-serif;
}

/* --- UI/UX DECISION --- st.metric nativo: agrandar font del valor */
[data-testid="stMetric"] {
    background: rgba(30,41,59,0.72);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
    padding: 14px 18px !important;
    box-shadow: 0 2px 10px rgba(0,0,0,0.30);
    backdrop-filter: blur(8px);
}
[data-testid="stMetricLabel"] {
    font-size: 0.78rem !important;
    font-weight: 600 !important;
    color: #94A3B8 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.06em !important;
    font-family: 'Inter', sans-serif !important;
}
[data-testid="stMetricValue"] {
    font-size: 1.6rem !important;
    font-weight: 700 !important;
    color: #F0FDF4 !important;
    letter-spacing: -0.03em !important;
    font-family: 'Inter', sans-serif !important;
}
[data-testid="stMetricDelta"] {
    font-size: 0.82rem !important;
    font-weight: 500 !important;
}

/* ════════════════════════════════════════════════════════════════════════════
   9. TABLAS / DATAFRAMES
   ════════════════════════════════════════════════════════════════════════════ */

/* --- UI/UX DECISION --- Tabla: header verde bosque (#005C2E), td con más padding,
   font 0.85rem (antes 0.78rem era demasiado pequeño para enterprise) */
[data-testid="stDataFrame"] {
    border-radius: 8px !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    overflow: hidden !important;
}
[data-testid="stDataFrame"] table {
    font-size: 0.85rem !important;
    font-family: 'Inter', sans-serif !important;
}
[data-testid="stDataFrame"] th {
    background: rgba(0,92,46,0.75) !important;
    color: #ECFDF5 !important;
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.06em !important;
    padding: 10px 14px !important;
    font-family: 'Inter', sans-serif !important;
}
[data-testid="stDataFrame"] td {
    padding: 9px 14px !important;
    border-bottom: 1px solid rgba(255,255,255,0.05) !important;
    font-size: 0.85rem !important;
    font-family: 'Inter', sans-serif !important;
    color: #CBD5E1 !important;
}
[data-testid="stDataFrame"] tr:hover td {
    background: rgba(0,168,107,0.07) !important;
}

/* ════════════════════════════════════════════════════════════════════════════
   10. SECTION HEADERS
   ════════════════════════════════════════════════════════════════════════════ */

.section-title {
    font-size: 0.8rem;
    font-weight: 700;
    color: #6EE7B7;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    border-bottom: 2px solid rgba(0,168,107,0.45);
    padding-bottom: 7px;
    margin: 24px 0 16px 0;
    font-family: 'Inter', sans-serif;
}

/* ════════════════════════════════════════════════════════════════════════════
   11. STATUS BOXES
   ════════════════════════════════════════════════════════════════════════════ */

/* --- UI/UX DECISION --- Boxes más grandes (padding 10px 16px),
   borde izquierdo 4px, fuente 0.875rem */
.box-ok {
    background: rgba(0,168,107,0.12); border-left: 4px solid #00A86B;
    border-radius: 0 6px 6px 0; padding: 10px 16px;
    margin: 8px 0; font-size: 0.875rem; color: #A7F3D0;
    line-height: 1.5;
}
.box-warn {
    background: rgba(217,119,6,0.12); border-left: 4px solid #D97706;
    border-radius: 0 6px 6px 0; padding: 10px 16px;
    margin: 8px 0; font-size: 0.875rem; color: #FDE68A;
    line-height: 1.5;
}
.box-err {
    background: rgba(220,38,38,0.12); border-left: 4px solid #DC2626;
    border-radius: 0 6px 6px 0; padding: 10px 16px;
    margin: 8px 0; font-size: 0.875rem; color: #FECACA;
    line-height: 1.5;
}
.box-info {
    background: rgba(30,64,175,0.15); border-left: 4px solid #3B82F6;
    border-radius: 0 6px 6px 0; padding: 10px 16px;
    margin: 8px 0; font-size: 0.875rem; color: #BFDBFE;
    line-height: 1.5;
}

/* ════════════════════════════════════════════════════════════════════════════
   12. TAGS / EXPANDERS / MISC
   ════════════════════════════════════════════════════════════════════════════ */

.tag { display: inline-block; border-radius: 4px;
       padding: 2px 8px; font-size: 0.72rem; font-weight: 600;
       margin: 0 2px; vertical-align: middle;
       font-family: 'Inter', sans-serif; }
.tag-green  { background: rgba(0,168,107,0.22); color: #6EE7B7; }
.tag-amber  { background: rgba(217,119,6,0.22); color: #FDE68A; }
.tag-red    { background: rgba(220,38,38,0.22); color: #FCA5A5; }
.tag-blue   { background: rgba(59,130,246,0.22); color: #93C5FD; }
.tag-gray   { background: rgba(100,116,139,0.22); color: #CBD5E1; }
.tag-teal   { background: rgba(20,184,166,0.22); color: #5EEAD4; }
.tag-citric { background: rgba(0,168,107,0.18); color: #A7F3D0; }

/* --- UI/UX DECISION --- Expanders: header 0.9rem, background blanco limpio */
.streamlit-expanderHeader,
[data-testid="stExpander"] summary {
    font-size: 0.9rem !important;
    font-weight: 600 !important;
    color: #CBD5E1 !important;
    background: rgba(30,41,59,0.75) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    border-radius: 6px !important;
    padding: 11px 16px !important;
    font-family: 'Inter', sans-serif !important;
}
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-top: none !important;
    border-radius: 0 0 6px 6px !important;
    padding: 14px 16px !important;
    background: rgba(15,23,42,0.55) !important;
}

/* ════════════════════════════════════════════════════════════════════════════
   13. VALUE HUD — Terminal financiera / Glassmorphism
   ════════════════════════════════════════════════════════════════════════════ */

/* Contenedor HUD: fondo oscuro slate, borde verde, tipografía mono */
.value-hud {
    background: linear-gradient(135deg, #0F172A 0%, #1E293B 100%);
    border: 1px solid rgba(0,168,107,0.35);
    border-radius: 12px;
    padding: 10px 16px;
    display: flex;
    align-items: stretch;
    gap: 0;
    margin-bottom: 14px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.35), inset 0 1px 0 rgba(0,168,107,0.15);
}
.hud-item {
    flex: 1;
    padding: 3px 12px;
    border-right: 1px solid rgba(0,168,107,0.12);
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
}
.hud-item:last-child { border-right: none; }
.hud-label {
    font-size: 0.68rem;
    color: #6EE7B7;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    display: block;
    margin-bottom: 3px;
    font-family: 'Inter', sans-serif;
}
.hud-value {
    font-size: 1.35rem;
    font-weight: 800;
    font-family: 'JetBrains Mono', monospace;
    color: #ECFDF5;
    white-space: nowrap;
    letter-spacing: -0.02em;
    line-height: 1.1;
}
.hud-value.positive { color: #4ADE80; }
.hud-value.negative { color: #F87171; }
.hud-value.neutral  { color: #94A3B8; }
.hud-badge {
    font-size: 0.72rem;
    color: #94A3B8;
    margin-top: 2px;
    display: block;
    font-family: 'Inter', sans-serif;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 100%;
    font-weight: 400;
}
.hud-badge.warn { color: #FCD34D; }

/* Glassmorphism panel — para resultado ganador */
.glass-winner {
    background: linear-gradient(135deg, rgba(0,122,62,0.25) 0%, rgba(0,168,107,0.12) 100%);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    border: 1.5px solid rgba(0,168,107,0.45);
    border-radius: 12px;
    padding: 16px 22px;
    margin-bottom: 16px;
}
.glass-winner.negative {
    background: linear-gradient(135deg, rgba(220,38,38,0.15) 0%, rgba(220,38,38,0.07) 100%);
    border-color: rgba(220,38,38,0.4);
}

/* Expert toggle pill */
.expert-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(15,23,42,0.08);
    border: 1px solid #E2E8F0;
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 0.72rem;
    font-weight: 600;
    color: #94A3B8;
    font-family: 'Inter', sans-serif;
    cursor: pointer;
}

/* SHA hash badge */
.sha-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: #0F172A;
    border: 1px solid rgba(0,168,107,0.3);
    border-radius: 6px;
    padding: 5px 12px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.62rem;
    color: #6EE7B7;
    letter-spacing: 0.04em;
}

/* ════════════════════════════════════════════════════════════════════════════
   13b. STATUS BAR FOOTER
   ════════════════════════════════════════════════════════════════════════════ */

.epm-statusbar {
    background: #0F172A;
    border-top: 2px solid #00A86B;
    padding: 8px 24px;
    font-size: 0.7rem;
    color: #94A3B8;
    font-family: 'JetBrains Mono', monospace;
    display: flex;
    gap: 28px;
    align-items: center;
    border-radius: 0 0 8px 8px;
    margin-top: 24px;
}
.epm-statusbar .status-item { display: flex; gap: 7px; align-items: center; }
.epm-statusbar .status-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: #00A86B; display: inline-block;
    box-shadow: 0 0 6px rgba(0, 168, 107, 0.6);
}

/* ════════════════════════════════════════════════════════════════════════════
   14. SCROLLBAR / SEPARADORES / MISC
   ════════════════════════════════════════════════════════════════════════════ */

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #F1F5F9; }
::-webkit-scrollbar-thumb { background: #B3EFD6; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #00A86B; }

hr { border: none; border-top: 1px solid #E2E8F0 !important; margin: 18px 0 !important; }

/* Quitar margen extra de Streamlit en columnas */
.stColumn { padding: 0 6px !important; }

/* Caption más legible */
.stCaption, [data-testid="stCaptionContainer"] {
    font-size: 0.78rem !important;
    color: #94A3B8 !important;
    line-height: 1.5 !important;
}

/* ════════════════════════════════════════════════════════════════════════════
   15. SCOPE CARDS
   ════════════════════════════════════════════════════════════════════════════ */

.scope-card {
    background: rgba(30,41,59,0.75);
    border: 2px solid rgba(255,255,255,0.10);
    border-radius: 12px;
    padding: 26px 22px 20px 22px;
    cursor: pointer;
    transition: all 0.2s ease;
    height: 100%;
    position: relative;
    overflow: hidden;
    backdrop-filter: blur(8px);
}
.scope-card::before {
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 5px;
    background: rgba(255,255,255,0.12);
    border-radius: 12px 12px 0 0;
    transition: background 0.2s ease;
}
.scope-card:hover {
    border-color: rgba(0,168,107,0.55);
    box-shadow: 0 8px 32px rgba(0,0,0,0.45);
    transform: translateY(-2px);
}
.scope-card:hover::before { background: #00A86B; }
.scope-card.active {
    border-color: rgba(0,168,107,0.65);
    background: rgba(0,168,107,0.12);
    box-shadow: 0 6px 24px rgba(0,168,107,0.20);
}
.scope-card.active::before { background: #00A86B; }
.scope-card.silo.active::before  { background: #3B82F6; }
.scope-card.enterprise.active::before { background: #14B8A6; }
.scope-card .scope-icon { font-size: 2.2rem; margin-bottom: 12px; }
.scope-card .scope-title {
    font-size: 0.95rem; font-weight: 700; color: #E2E8F0;
    margin-bottom: 6px; letter-spacing: -0.02em;
    font-family: 'Inter', sans-serif;
}
.scope-card .scope-desc {
    font-size: 0.8rem; color: #94A3B8; line-height: 1.55;
    font-family: 'Inter', sans-serif;
}
.scope-card .scope-action {
    margin-top: 16px; font-size: 0.74rem; font-weight: 700;
    color: #6EE7B7; letter-spacing: 0.06em; text-transform: uppercase;
    font-family: 'Inter', sans-serif;
}

/* ════════════════════════════════════════════════════════════════════════════
   16. RADAR PANEL / LANDING WELCOME
   ════════════════════════════════════════════════════════════════════════════ */

.radar-panel {
    background: rgba(30,41,59,0.70); border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px; padding: 20px; height: 100%;
    backdrop-filter: blur(8px);
}

.epm-welcome { text-align: center; padding: 48px 24px 36px 24px; }
.epm-welcome .welcome-title {
    font-size: 1.8rem; font-weight: 800; color: #6EE7B7;
    letter-spacing: -0.03em; margin-bottom: 8px;
    font-family: 'Inter', sans-serif;
}
.epm-welcome .welcome-sub {
    font-size: 0.9rem; color: #94A3B8; margin-bottom: 32px;
    line-height: 1.6; font-family: 'Inter', sans-serif;
}

/* ════════════════════════════════════════════════════════════════════════════
   17. SIDEBAR — File uploader contrast (dark bg fix)
   ════════════════════════════════════════════════════════════════════════════ */

[data-testid="stSidebar"] [data-testid="stFileUploader"] section,
[data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"] {
    background: rgba(0, 168, 107, 0.10) !important;
    border: 1.5px dashed rgba(0, 168, 107, 0.55) !important;
    border-radius: 8px !important;
}
[data-testid="stSidebar"] [data-testid="stFileUploader"] p,
[data-testid="stSidebar"] [data-testid="stFileUploader"] span,
[data-testid="stSidebar"] [data-testid="stFileUploader"] small,
[data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stMarkdownContainer"] {
    color: #A7F3D0 !important;
}
[data-testid="stSidebar"] [data-testid="stFileUploader"] button {
    color: #ECFDF5 !important;
    border: 1px solid rgba(0, 168, 107, 0.55) !important;
    background: rgba(0, 168, 107, 0.22) !important;
    border-radius: 5px !important;
}
[data-testid="stSidebar"] [data-testid="stFileUploader"] button:hover {
    background: rgba(0, 168, 107, 0.38) !important;
}

/* ════════════════════════════════════════════════════════════════════════════
   18. SIDEBAR — Expanders visibles sobre fondo oscuro
   ════════════════════════════════════════════════════════════════════════════ */

[data-testid="stSidebar"] [data-testid="stExpander"] summary {
    color: #ECFDF5 !important;
    background: rgba(0, 168, 107, 0.18) !important;
    border: 1px solid rgba(0, 168, 107, 0.35) !important;
    font-size: 0.88rem !important;
    font-weight: 700 !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {
    background: rgba(0, 168, 107, 0.28) !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary svg {
    fill: #6EE7B7 !important;
}
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] {
    background: rgba(0, 20, 10, 0.35) !important;
    border: 1px solid rgba(0, 168, 107, 0.25) !important;
    border-top: none !important;
    border-radius: 0 0 6px 6px !important;
}
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] [data-testid="stWidgetLabel"] p,
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] label,
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] .stSlider > label {
    color: #D1FAE5 !important;
}
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] input[type="number"] {
    background: rgba(0, 168, 107, 0.12) !important;
    color: #ECFDF5 !important;
    border-color: rgba(0, 168, 107, 0.4) !important;
}

/* ════════════════════════════════════════════════════════════════════════════
   19. DROPDOWNS — Prevent text truncation en selected value y opciones
   ════════════════════════════════════════════════════════════════════════════ */

[data-testid="stSelectbox"] [data-baseweb="select"] > div {
    height: auto !important;
    min-height: 46px !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] > div > div:first-child {
    overflow: visible !important;
    white-space: normal !important;
    text-overflow: clip !important;
    word-break: break-word !important;
    padding: 6px 0 6px 14px !important;
    flex: 1 1 auto !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] > div > div > div,
[data-testid="stSelectbox"] [data-baseweb="select"] > div > div > span {
    overflow: visible !important;
    white-space: normal !important;
    text-overflow: clip !important;
    word-break: break-word !important;
    line-height: 1.35 !important;
}
[data-baseweb="menu"] [role="option"] span,
[data-baseweb="popover"] [role="option"] span {
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: clip !important;
    word-break: break-word !important;
}

/* ════════════════════════════════════════════════════════════════════════════
   20. NUMBER INPUTS — Alineación vertical cuando labels tienen distinto alto
   ════════════════════════════════════════════════════════════════════════════ */

[data-testid="stNumberInput"] > label,
[data-testid="stNumberInput"] [data-testid="stWidgetLabel"] {
    min-height: 2.6rem !important;
    display: flex !important;
    align-items: flex-end !important;
    padding-bottom: 4px !important;
}

/* ════════════════════════════════════════════════════════════════════════════
   21. ANATOMY BAR — Tira horizontal de la ecuación VNT en el header
   ════════════════════════════════════════════════════════════════════════════ */

.anatomy-bar {
    background: linear-gradient(135deg, #005C2E 0%, #007A3E 60%, #00A86B 100%);
    border-radius: 8px;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    gap: 0;
    flex-wrap: wrap;
    margin-bottom: 16px;
    box-shadow: 0 2px 8px rgba(0, 92, 46, 0.22);
}
.anatomy-bar .ab-label {
    font-size: 0.6rem;
    font-weight: 700;
    color: #6EE7B7;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    margin-right: 10px;
    white-space: nowrap;
    font-family: 'Inter', sans-serif;
    padding-right: 10px;
    border-right: 1px solid rgba(110, 231, 183, 0.3);
}
.anatomy-bar .ab-eq {
    display: flex;
    align-items: center;
    flex: 1;
    flex-wrap: wrap;
    gap: 0;
}
.anatomy-bar .ab-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 0 10px;
    border-right: 1px solid rgba(110, 231, 183, 0.18);
    min-width: 72px;
}
.anatomy-bar .ab-item:last-child { border-right: none; }
.anatomy-bar .ab-op {
    font-size: 0.58rem;
    color: rgba(167, 243, 208, 0.7);
    font-weight: 600;
    font-family: 'JetBrains Mono', monospace;
    min-height: 13px;
}
.anatomy-bar .ab-val {
    font-size: 0.88rem;
    font-weight: 700;
    color: #ECFDF5;
    font-family: 'JetBrains Mono', monospace;
    white-space: nowrap;
}
.anatomy-bar .ab-desc {
    font-size: 0.56rem;
    color: #A7F3D0;
    margin-top: 1px;
    text-align: center;
    font-family: 'Inter', sans-serif;
}
.anatomy-bar .ab-vnt {
    margin-left: 14px;
    padding-left: 14px;
    border-left: 2px solid rgba(110, 231, 183, 0.45);
    display: flex;
    flex-direction: column;
}
.anatomy-bar .ab-vnt-lbl {
    font-size: 0.56rem;
    color: #6EE7B7;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-family: 'Inter', sans-serif;
}
.anatomy-bar .ab-vnt-val {
    font-size: 1.1rem;
    font-weight: 800;
    font-family: 'JetBrains Mono', monospace;
    letter-spacing: -0.02em;
    white-space: nowrap;
}

/* ════════════════════════════════════════════════════════════════════════════
   22. SCOPE CARDS — decorative only, btn is the action
   ════════════════════════════════════════════════════════════════════════════ */

.scope-card { cursor: default !important; }

/* ════════════════════════════════════════════════════════════════════════════
   23. INDUSTRIAL DECISION CONSOLE — Premium Dark v3.0
   Deep Slate #0F172A · Glassmorphism · Header Kill · Sidebar Elite 400px
   Anti-Hyphen · Sticky HUD · EPM Green → Electric Lime gradients
   ════════════════════════════════════════════════════════════════════════════ */

/* ── FONDO GLOBAL ─────────────────────────────────────────────────────────── */
.stApp,
section[data-testid="stMain"] { background: #0F172A !important; }

/* ── HEADER NATIVO: ELIMINADO — recupera el espacio para contenido ─────────── */
header[data-testid="stHeader"],
.stAppHeader,
#MainMenu,
footer {
    visibility: hidden !important;
    height: 0 !important;
    min-height: 0 !important;
    padding: 0 !important;
    margin: 0 !important;
    overflow: hidden !important;
}
.stApp > header { display: none !important; }
.main > div:first-child { padding-top: 0 !important; }
[data-testid="stAppViewContainer"] { padding-top: 0 !important; }

/* ── SIDEBAR INDUSTRIAL ELITE ─────────────────────────────────────────────── */
/* Streamlit controla el ancho del sidebar via JavaScript.
   Solo aplicamos estilos visuales — NO tocamos width/min-width/max-width.
   Cualquier override de ancho compite con el cálculo de margin-left del contenido
   y produce el efecto de invasión visible en pantalla. */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1E293B 0%, #0F172A 100%) !important;
    border-right: 1px solid rgba(255,255,255,0.08) !important;
}
[data-testid="stSidebar"] > div:first-child {
    overflow-y: auto !important;
    overflow-x: hidden !important;
    padding-bottom: 2rem !important;
}

/* Texto sidebar: blanco puro, peso 600 */
[data-testid="stSidebar"] .stMarkdown,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
    color: #FFFFFF !important;
    font-weight: 600 !important;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] h4 { color: #FFFFFF !important; font-weight: 700 !important; }
[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
    color: rgba(255,255,255,0.55) !important;
    font-weight: 400 !important;
}
/* Selectbox en sidebar */
[data-testid="stSidebar"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    background: rgba(255,255,255,0.08) !important;
    border-color: rgba(255,255,255,0.18) !important;
}
[data-testid="stSidebar"] [data-testid="stSelectbox"] span,
[data-testid="stSidebar"] [data-testid="stSelectbox"] [data-testid="stMarkdownContainer"] p {
    color: #FFFFFF !important;
    font-weight: 600 !important;
}
/* Toggle en sidebar */
[data-testid="stSidebar"] .stToggle label { color: #FFFFFF !important; font-weight: 600 !important; }
/* Botones sidebar */
[data-testid="stSidebar"] .stButton > button {
    background: rgba(255,255,255,0.07) !important;
    color: #FFFFFF !important;
    border: 1px solid rgba(255,255,255,0.18) !important;
    font-weight: 600 !important;
    transition: all 0.15s ease !important;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: rgba(0,168,107,0.28) !important;
    border-color: #00A86B !important;
    color: #ECFDF5 !important;
}
[data-testid="stSidebar"] .stButton > button[data-active="true"],
[data-testid="stSidebar"] .stButton > button.bu-active {
    background: linear-gradient(135deg, #00A86B 0%, #059669 100%) !important;
    border-color: #00A86B !important;
    color: #FFFFFF !important;
    box-shadow: 0 0 12px rgba(0,168,107,0.35) !important;
}
[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.1) !important; }
/* File uploader sidebar */
[data-testid="stSidebar"] [data-testid="stFileUploader"] {
    background: rgba(255,255,255,0.05) !important;
    border: 1.5px dashed rgba(255,255,255,0.25) !important;
    border-radius: 8px !important;
}
[data-testid="stSidebar"] [data-testid="stFileUploader"] label {
    color: #FFFFFF !important;
    font-weight: 600 !important;
}
/* Logo sidebar */
.epm-sidebar-logo {
    background: rgba(0,0,0,0.35) !important;
    border-bottom: 1px solid rgba(255,255,255,0.1) !important;
}
/* Scrollbar sidebar */
[data-testid="stSidebar"]::-webkit-scrollbar-track { background: #0F172A !important; }
[data-testid="stSidebar"]::-webkit-scrollbar-thumb {
    background: rgba(255,255,255,0.15) !important;
    border-radius: 4px !important;
}

/* ── ANTI-HYPHEN — quirúrgico: solo elementos de texto, nunca contenedores ── */
h1, h2, h3, h4, h5, h6,
[data-testid="stWidgetLabel"],
[data-testid="stWidgetLabel"] p,
.stSelectbox label, .stTextInput label,
.stNumberInput label, .stSlider label,
.stCheckbox label, .stRadio label,
.kpi-label, .hud-label, .section-title,
.rail-section-label, .scope-title,
.epm-page-header .page-title,
.epm-sidebar-logo .logo-main {
    hyphens: none !important;
    word-break: keep-all !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}
/* Sidebar: anti-hyphen SOLO en elementos de texto explícitos, NO wildcard * */
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] button span,
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
[data-testid="stSidebar"] .rail-section-label,
[data-testid="stSidebar"] .rpc-title {
    hyphens: none !important;
    word-break: keep-all !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}
/* Captions y párrafos largos: wrapping normal */
[data-testid="stSidebar"] .stCaption p,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
[data-testid="stSidebar"] .rpc-meta {
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: unset !important;
    hyphens: auto !important;
}

/* ── TABS ─────────────────────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    background: rgba(2,8,23,0.95) !important;
    border-bottom: 2px solid rgba(0,168,107,0.5) !important;
    backdrop-filter: blur(8px) !important;
}
.stTabs [data-baseweb="tab"] { color: #94A3B8 !important; }
.stTabs [data-baseweb="tab"]:hover {
    background: rgba(0,168,107,0.08) !important;
    color: #6EE7B7 !important;
}
.stTabs [aria-selected="true"] {
    color: #6EE7B7 !important;
    background: rgba(0,168,107,0.1) !important;
    border-bottom-color: #00A86B !important;
}
/* Tab panel glassmorphism */
.stTabs [data-baseweb="tab-panel"] {
    background: rgba(15,23,42,0.88) !important;
    backdrop-filter: blur(12px) !important;
    -webkit-backdrop-filter: blur(12px) !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    border-radius: 0 0 16px 16px !important;
    border-top: none !important;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5) !important;
}

/* ── KPI CARDS — GLASSMORPHISM ────────────────────────────────────────────── */
.kpi-card {
    background: rgba(30,41,59,0.72) !important;
    backdrop-filter: blur(12px) !important;
    -webkit-backdrop-filter: blur(12px) !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 16px !important;
    box-shadow: 0 4px 24px rgba(0,0,0,0.45),
                inset 0 1px 0 rgba(255,255,255,0.06) !important;
}
.kpi-label { color: #94A3B8 !important; }
.kpi-value { color: #F0FDF4 !important; }
.kpi-sub   { color: #94A3B8 !important; }
.kpi-card.amber .kpi-value { color: #FCD34D !important; }
.kpi-card.red   .kpi-value { color: #F87171 !important; }
.kpi-card.teal  .kpi-value { color: #5EEAD4 !important; }
.kpi-card.blue  .kpi-value { color: #93C5FD !important; }

/* KPI Gradient EPM Green → Electric Lime */
.kpi-gradient-value {
    background: linear-gradient(135deg, #00A86B 0%, #84CC16 100%);
    -webkit-background-clip: text !important;
    -webkit-text-fill-color: transparent !important;
    background-clip: text !important;
    font-size: 1.75rem !important;
    font-weight: 800 !important;
    font-family: 'JetBrains Mono', monospace !important;
    letter-spacing: -0.03em !important;
}

/* ── ST.METRIC NATIVO ─────────────────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: rgba(30,41,59,0.72) !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 16px !important;
    backdrop-filter: blur(10px) !important;
    box-shadow: 0 4px 16px rgba(0,0,0,0.35),
                inset 0 1px 0 rgba(255,255,255,0.05) !important;
}
[data-testid="stMetricLabel"] { color: #94A3B8 !important; }
[data-testid="stMetricValue"] { color: #F0FDF4 !important; }
[data-testid="stMetricDelta"] svg { fill: currentColor !important; }

/* ── TIPOGRAFÍA DARK ──────────────────────────────────────────────────────── */
p, li, span { color: #CBD5E1; }
.stMarkdown p { color: #94A3B8 !important; }
h1 { color: #F0FDF4 !important; }
h2 { color: #E2E8F0 !important; }
h3 { color: #CBD5E1 !important; }
h4, h5, h6 { color: #94A3B8 !important; }
[data-testid="stWidgetLabel"] p,
.stSelectbox label, .stTextInput label,
.stNumberInput label, .stSlider label,
.stCheckbox label, .stRadio label { color: #94A3B8 !important; }

/* ── INPUTS DARK GLASS ────────────────────────────────────────────────────── */
.stTextInput > div > div > input,
.stNumberInput > div > div > input {
    background: rgba(30,41,59,0.85) !important;
    color: #E2E8F0 !important;
    border-color: rgba(255,255,255,0.12) !important;
}
.stTextInput > div > div > input:focus,
.stNumberInput > div > div > input:focus {
    border-color: #00A86B !important;
    box-shadow: 0 0 0 3px rgba(0,168,107,0.18) !important;
}
.stTextArea > div > div > textarea {
    background: rgba(30,41,59,0.85) !important;
    color: #E2E8F0 !important;
    border-color: rgba(255,255,255,0.12) !important;
}

/* ── SELECTBOX DARK GLASS ─────────────────────────────────────────────────── */
[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
[data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover {
    background: rgba(30,41,59,0.88) !important;
    border-color: rgba(255,255,255,0.12) !important;
}
[data-testid="stSelectbox"] div[data-baseweb="select"] span,
[data-testid="stSelectbox"] div[data-baseweb="select"] [data-testid="stMarkdownContainer"] p {
    color: #E2E8F0 !important;
}
[data-baseweb="menu"], [data-baseweb="popover"] {
    background: #1E293B !important;
    border-color: rgba(255,255,255,0.1) !important;
}
[data-baseweb="menu"] li,
[data-baseweb="menu"] [role="option"],
[data-baseweb="popover"] [role="option"] { background: #1E293B !important; color: #CBD5E1 !important; }
[data-baseweb="menu"] [role="option"]:hover,
[data-baseweb="menu"] li:hover { background: rgba(0,168,107,0.15) !important; color: #6EE7B7 !important; }
[data-baseweb="menu"] [aria-selected="true"] {
    background: rgba(0,168,107,0.2) !important;
    color: #6EE7B7 !important;
}

/* ── EXPANDER ─────────────────────────────────────────────────────────────── */
.streamlit-expanderHeader,
[data-testid="stExpander"] summary {
    background: rgba(30,41,59,0.8) !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    color: #E2E8F0 !important;
}
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
    background: rgba(15,23,42,0.9) !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
}

/* ── BOXES DE ESTADO ──────────────────────────────────────────────────────── */
.box-ok   { background: rgba(0,168,107,0.12) !important;  color: #D1FAE5 !important; }
.box-warn { background: rgba(217,119,6,0.12) !important;  color: #FEF3C7 !important; }
.box-err  { background: rgba(220,38,38,0.12) !important;  color: #FEE2E2 !important; }
.box-info { background: rgba(30,64,175,0.12) !important;  color: #DBEAFE !important; }

/* ── DATAFRAME ────────────────────────────────────────────────────────────── */
[data-testid="stDataFrame"] {
    background: rgba(15,23,42,0.9) !important;
    border-color: rgba(255,255,255,0.08) !important;
}
[data-testid="stDataFrame"] td {
    background: transparent !important;
    color: #E2E8F0 !important;
    border-bottom-color: rgba(255,255,255,0.05) !important;
}
[data-testid="stDataFrame"] tr:hover td { background: rgba(0,168,107,0.08) !important; }

/* ── ELEMENTOS DE MARCA ───────────────────────────────────────────────────── */
.section-title { color: #6EE7B7 !important; border-bottom-color: rgba(0,168,107,0.5) !important; }
.stCaption, [data-testid="stCaptionContainer"] { color: #94A3B8 !important; }
hr { border-top-color: rgba(255,255,255,0.08) !important; }
.stRadio > div > label { color: #94A3B8 !important; }
.stRadio > div > label:hover { color: #00A86B !important; }
::-webkit-scrollbar-track { background: #0F172A !important; }
::-webkit-scrollbar-thumb { background: rgba(0,168,107,0.35) !important; border-radius: 4px !important; }
::-webkit-scrollbar-thumb:hover { background: rgba(0,168,107,0.6) !important; }

/* ── TAGS ─────────────────────────────────────────────────────────────────── */
.tag-green  { background: rgba(0,168,107,0.18) !important; color: #6EE7B7 !important; }
.tag-amber  { background: rgba(217,119,6,0.18) !important; color: #FCD34D !important; }
.tag-red    { background: rgba(220,38,38,0.18) !important; color: #F87171 !important; }
.tag-blue   { background: rgba(30,64,175,0.18) !important; color: #93C5FD !important; }
.tag-gray   { background: rgba(71,85,105,0.25) !important; color: #94A3B8 !important; }
.tag-citric { background: rgba(0,92,46,0.25)   !important; color: #6EE7B7 !important; }

/* ── STREAMLIT CONTAINER BACKGROUNDS — eliminar blancos nativos ───────────── */
.stHorizontalBlock,
.stVerticalBlock,
[data-testid="stHorizontalBlock"],
[data-testid="stVerticalBlock"],
[data-testid="column"],
[data-testid="stMainBlockContainer"],
.element-container,
div[data-testid="stMarkdownContainer"] {
    background: transparent !important;
}
/* Success/info/warning/error messages → dark glass */
[data-testid="stAlert"] {
    background: rgba(30,41,59,0.88) !important;
    border-color: rgba(255,255,255,0.12) !important;
    color: #E2E8F0 !important;
}

/* ── COMPONENTES ESPECIALES ───────────────────────────────────────────────── */
.anatomy-bar  { box-shadow: 0 4px 24px rgba(0,168,107,0.25) !important; }
.glass-winner { border: 1.5px solid rgba(0,168,107,0.5) !important; box-shadow: 0 8px 32px rgba(0,168,107,0.15) !important; }
.radar-panel  { background: rgba(30,41,59,0.75) !important; border-color: rgba(255,255,255,0.1) !important; backdrop-filter: blur(8px) !important; }
.scope-card   { background: rgba(30,41,59,0.75) !important; border-color: rgba(255,255,255,0.1) !important; backdrop-filter: blur(8px) !important; }
.scope-card .scope-title { color: #E2E8F0 !important; }
.scope-card .scope-desc  { color: #94A3B8 !important; }
.scope-card:hover        { border-color: #00A86B !important; }
.scope-card.active       { background: rgba(0,168,107,0.1) !important; border-color: #00A86B !important; }

/* ── STICKY HUD ───────────────────────────────────────────────────────────── */
.value-hud {
    position: sticky !important;
    top: 0 !important;
    z-index: 998 !important;
    margin-top: 0 !important;
}

/* ── BADGES EXECUTIVE / EXPERT ────────────────────────────────────────────── */
body.executive-mode .expert-only { display: none !important; }
.exec-badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: linear-gradient(135deg, #1E40AF 0%, #7C3AED 100%);
    border-radius: 20px; padding: 4px 14px;
    font-size: 0.72rem; font-weight: 700; color: #FFFFFF;
    font-family: 'Inter', sans-serif; letter-spacing: 0.04em;
}
.expert-badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: linear-gradient(135deg, #007A3E 0%, #00A86B 100%);
    border-radius: 20px; padding: 4px 14px;
    font-size: 0.72rem; font-weight: 700; color: #FFFFFF;
    font-family: 'Inter', sans-serif; letter-spacing: 0.04em;
}

/* ── CAPACITY GAP ─────────────────────────────────────────────────────────── */
.capacity-gap-container {
    background: rgba(30,41,59,0.72);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 16px;
    padding: 20px;
    margin: 16px 0;
}

/* ════════════════════════════════════════════════════════════════════════════
   UX PRO — Accessibility, Interaction & Focus (WCAG AA compliant)
   ════════════════════════════════════════════════════════════════════════════ */

/* 1. cursor-pointer: todos los elementos interactivos deben señalizarlo */
.stTabs [data-baseweb="tab"]          { cursor: pointer !important; }
.stRadio > div > label                { cursor: pointer !important; }
[data-testid="stSidebar"] [data-testid="stExpander"] summary { cursor: pointer !important; }
[data-testid="stExpander"] summary    { cursor: pointer !important; }
[data-baseweb="menu"] [role="option"] { cursor: pointer !important; }
[data-baseweb="menu"] li              { cursor: pointer !important; }
[data-testid="stCheckbox"] label      { cursor: pointer !important; }
.stSlider [role="slider"]             { cursor: grab !important; }
.stSlider [role="slider"]:active      { cursor: grabbing !important; }

/* 2. Focus-visible rings (keyboard navigation) — WCAG AA §2.4.7 */
.stTabs [data-baseweb="tab"]:focus-visible {
    outline: 2px solid #00A86B !important;
    outline-offset: -2px !important;
    border-radius: 4px !important;
}
.stButton > button:focus-visible,
[data-testid="stSidebar"] .stButton > button:focus-visible {
    outline: 2px solid #00A86B !important;
    outline-offset: 2px !important;
}
.stCheckbox label:focus-visible,
.stRadio > div > label:focus-visible  {
    outline: 2px solid rgba(0,168,107,0.6) !important;
    outline-offset: 2px !important;
    border-radius: 4px !important;
}
[data-testid="stExpander"] summary:focus-visible {
    outline: 2px solid #00A86B !important;
    outline-offset: 2px !important;
    border-radius: 6px !important;
}
[data-testid="stSelectbox"] div[data-baseweb="select"]:focus-within {
    outline: 2px solid rgba(0,168,107,0.4) !important;
    border-radius: 6px !important;
}

/* 3. Disabled state — botones deshabilitados claramente visibles */
.stButton > button:disabled,
.stButton > button[disabled] {
    opacity: 0.38 !important;
    cursor: not-allowed !important;
    pointer-events: none !important;
    filter: grayscale(0.4) !important;
}
[data-testid="stSidebar"] .stButton > button:disabled,
[data-testid="stSidebar"] .stButton > button[disabled] {
    opacity: 0.38 !important;
    cursor: not-allowed !important;
}

/* 4. Scope-card hover signal: transition hint (card es decorativa — btn es la acción) */
.scope-card { transition: border-color 0.15s ease, box-shadow 0.15s ease !important; }

/* 5. Alert messages — mejor contraste de texto */
[data-testid="stAlert"] p,
[data-testid="stAlert"] span { color: #E2E8F0 !important; }
[data-testid="stAlert"][data-baseweb="notification"][kind="error"] {
    border-color: rgba(239,68,68,0.55) !important;
}
[data-testid="stAlert"][data-baseweb="notification"][kind="warning"] {
    border-color: rgba(245,158,11,0.55) !important;
}
[data-testid="stAlert"][data-baseweb="notification"][kind="success"] {
    border-color: rgba(0,168,107,0.55) !important;
}

/* 6. prefers-reduced-motion — respetar preferencia del sistema */
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }
    .kpi-card:hover    { transform: none !important; }
    .scope-card:hover  { transform: none !important; }
    .stButton > button:hover { transform: none !important; }
}

/* 7. Texto mínimo legible — pequeñas etiquetas deben tener line-height adecuado */
[data-testid="stCaptionContainer"] * { line-height: 1.45 !important; }
.kpi-sub, .kpi-label { line-height: 1.35 !important; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# SESSION STATE — inicializar todas las claves
# ══════════════════════════════════════════════════════════════════════════════
DEFAULTS = {
    "config":           None,
    "projects":         [],
    "portfolio_report": {},
    "eval_results":     [],
    "opt_result":       None,
    "deg_results":      {},
    "snapshot_store":   None,
    "last_upload":      None,
    "periods":          [],
    # ── v9.0: Consola de Decisión Estratégica ────────────────────────────────
    "decision_scope":    None,   # None | "local" | "silo" | "enterprise"
    "wizard_results":    [],     # marginal_results del último Duelo de Valor
    "wizard_r_base":     None,   # r_base del último Duelo de Valor
    # v16.0 alternatives tracking
    "w_has_alternatives": False,
    "w_alt_count":        1,   # default: 1 alternativa vs Do Nothing (caso más frecuente)
    "w_current_alt_idx":  0,
    "w_alts_done":        [],    # [{letter, vnt_preview_mm, total_COP_year, reto_inputs}]
    # v17.1 proporcionalidad automática
    "w_capex_ref":        0.0,   # CAPEX referencia ingresado en Step 1 (MM COP)
    "w_model_level":      "",    # RULE_BASED | SIMPLIFIED | FULL (calculado automático)
    "w_checklist":        {},    # respuestas checklist RULE_BASED por alternativa
    "w_sensitivity":      {"wacc_pct": 2, "ben_pct": 20, "capex_pct": 10},
    # v17.2 familia de activo + madurez de datos (Data Maturity Copperleaf C55)
    "w_asset_family":     "otro",          # clave de ASSET_FAMILIES
    "w_data_level":       "driver_based",  # asset_aware | driver_based | concept_rating
    # D-14 componentes múltiples de inacción: horizonte único, sin default —
    # None obliga al analista a declararlo explícitamente en Paso 1.
    "w_analysis_horizon_years": None,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── Proporcionalidad: helper que determina el nivel automáticamente ────────────
def _wizard_model_level(capex_mm: float, bu: str, cfg: dict) -> str:
    """
    Determina el nivel de análisis según CAPEX + BU.
    Lee umbrales de proportionality_thresholds_COP del config.
    FULL ≥ umbral_full · SIMPLIFIED ≥ umbral_simp · RULE_BASED por debajo.
    """
    thr = cfg.get("proportionality_thresholds_COP", {})
    bu_t = thr.get(bu) or thr.get("Todos") or {}
    full_cop = bu_t.get("full_model_COP",       10_000_000_000)
    simp_cop = bu_t.get("simplified_model_COP",  1_000_000_000)
    capex_cop = capex_mm * 1_000_000
    if capex_cop >= full_cop:  return "FULL"
    if capex_cop >= simp_cop:  return "SIMPLIFIED"
    return "RULE_BASED"


_ML_META = {
    "RULE_BASED":  {
        "label":  "RULE_BASED — Checklist de criterios",
        "icon":   "📋",
        "color":  EPM_TEAL,
        "bg":     "#F0FDFA",
        "border": "#99F6E4",
        "desc":   "CAPEX pequeño: se aprueba por criterios categóricos, sin VNT individual.",
        "gov":    "Gerente de operaciones",
    },
    "SIMPLIFIED":  {
        "label":  "SIMPLIFIED — Modelo simplificado",
        "icon":   "🟢",
        "color":  EPM_DARK,
        "bg":     "#F0FDF4",
        "border": "#B3EFD6",
        "desc":   "VNT + VEI con estimado puntual. Sin análisis de sensibilidad ni MILP.",
        "gov":    "VP de la unidad de negocio",
    },
    "FULL":        {
        "label":  "FULL — Modelo completo",
        "icon":   "🔵",
        "color":  EPM_BLUE,
        "bg":     "#EFF6FF",
        "border": "#BFDBFE",
        "desc":   "VNT + sensibilidad + escenarios + Optimal Timing. Defense Report obligatorio.",
        "gov":    "Comité de inversiones",
    },
}

# ── Loaders con caché ─────────────────────────────────────────────────────────
@st.cache_resource
def get_config() -> dict:
    """Carga config.json desde el mismo directorio que este script.

    Fusiona con DEFAULT_CONFIG lo que el archivo no tenga. Sin esa fusión, un
    config guardado con una versión anterior del motor deja silenciosamente
    desactivada toda capacidad agregada después — el `.get(clave, {})` devuelve
    vacío y la app sigue funcionando como si el parámetro no existiera.
    El archivo siempre gana: la fusión solo agrega ausentes.

    Fallback a load_config() del core si el archivo no existe.
    """
    import json as _json
    _cfg_path = Path(__file__).parent / "config.json"
    if _cfg_path.exists():
        with open(_cfg_path, encoding="utf-8") as _f:
            _raw = _json.load(_f)
        _merged, _added = _merge_config_defaults(_raw)
        if _added:
            st.session_state["_config_defaults_added"] = _added
        # v3.7.0: el catálogo de sub-criterios por negocio ya no está hardcodeado.
        apply_bu_driver_config(_merged)
        return _merged
    # fallback: core load_config() (busca config.json, ya fusiona)
    _cfg = load_config()
    apply_bu_driver_config(_cfg)
    return _cfg

def _closure_kwargs_for(project_id: str, action: dict) -> dict:
    """Arma los kwargs de cierre para WorkflowEngine.apply() cuando la acción
    lleva a COMPLETADO. Fuera de ese caso devuelve {} — apply() ya trata la
    ausencia de estos datos como no-op, así que no hay rama especial que
    mantener en cada call site.

    Busca el ProjectInput en session_state["projects"] y el costo final en
    session_state["evm_actuals"] (ac_COP del ProjectActual con pct_complete
    más alto para ese ID — el más reciente si hay varios cortes). Si falta
    cualquiera de los dos, el cierre no se registra: es preferible perder una
    observación a bloquear la transición de estado del usuario por un dato
    de EVM que puede no estar cargado en esta sesión.
    """
    if action.get("to") != "COMPLETADO":
        return {}
    project = next(
        (p for p in st.session_state.get("projects", []) if p.id == project_id),
        None)
    if project is None:
        return {}
    actuals = [a for a in st.session_state.get("evm_actuals", [])
               if getattr(a, "project_id", None) == project_id]
    if not actuals:
        return {}
    final = max(actuals, key=lambda a: getattr(a, "pct_complete", 0.0))
    if getattr(final, "ac_COP", 0.0) <= 0:
        return {}
    return {
        "project": project,
        "capex_final_COP": final.ac_COP,
        "accuracy_store": get_accuracy_store(),
    }


def get_accuracy_store() -> EstimateAccuracyStore:
    """Store de precisión de estimativos — un cierre de proyecto = una fila.

    Es la fuente de la que se calibran las bandas propias de EPM en
    cost_estimate_uncertainty.classes_by_bu. Mientras no haya muestra
    suficiente (ver readiness_report), el modelo sigue usando la referencia
    de industria; este store es lo que hace que eso deje de ser cierto.
    """
    if "accuracy_store" not in st.session_state:
        _acc_path = Path(__file__).parent / "estimate_accuracy"
        st.session_state["accuracy_store"] = EstimateAccuracyStore(str(_acc_path))
    return st.session_state["accuracy_store"]


@st.cache_data(show_spinner=False)
def _cached_enrich(projects_hash: str, config_hash: str) -> List[dict]:
    """Enriquecer proyectos con VNT — cacheado por hash del portafolio."""
    cfg = get_config()
    loader = PortfolioLoader(cfg)
    return loader.enrich_with_vnt(st.session_state["projects"])

def _projects_hash() -> str:
    ids = [p.id + str(p.costs.capex_COP) for p in st.session_state["projects"]]
    return hashlib.md5("|".join(ids).encode()).hexdigest()[:12]

def _config_hash() -> str:
    return hashlib.md5(json.dumps(get_config(), default=str).encode()).hexdigest()[:12]

def fmt_cop(v: float, decimals: int = 0) -> str:
    """Formatea valor COP en MM con separador de miles."""
    mm = v / 1_000_000
    if abs(mm) >= 1_000_000:
        return f"${mm/1_000_000:,.{decimals}f}B"
    elif abs(mm) >= 1_000:
        return f"${mm/1_000:,.{decimals}f}MM (miles)"
    return f"${mm:,.{decimals}f} MM"


def _weibull_pof_at_age(beta: float, eta: float, age: float) -> float:
    """PoF acumulada Weibull F(t) = 1 − exp(−(t/η)^β) a la edad `age`.

    Devuelve 0.0 si la curva no está definida (β o η ≤ 0) o la edad es < 0.
    Es la misma fórmula que usa el asistente en FASE A (Do Nothing) para el
    PoF actual del activo — extraída a nivel de módulo para poder sumar el
    aporte de varios componentes en FASE B.
    """
    try:
        if beta > 0 and eta > 0 and age >= 0:
            return 1.0 - math.exp(-((age / eta) ** beta))
    except (OverflowError, ValueError, ZeroDivisionError):
        return 0.0
    return 0.0


def _asset_aware_eal_avoided_COP(components, int_type: str,
                                 age_effective_new: float = 0.0):
    """Suma, sobre TODOS los componentes asset_aware, la EAL evitada (COP/año)
    por una intervención — el beneficio en pesos que FASE B inyecta como
    cantidad del driver VD-1 y que por tanto alimenta el VNT de la alternativa.

    Antes esto se calculaba con un único `st.session_state["w_aw_baseline"]`
    (los β/η/edad/CoF del ÚLTIMO componente declarado), por lo que con 2+
    componentes todos menos ese aportaban CERO. Ahora cada componente aporta
    su propia física.

    Parámetros
    ----------
    components : iterable de RiskInput
        Se consideran sólo los que llevan curva Weibull propia poblada
        (`weibull_beta`, `weibull_eta`, `weibull_cof_COP` > 0). Los componentes
        sin curva (driver_based / modo directo) aportan 0 y se omiten del
        desglose.
    int_type : "reposicion" | "rehabilitacion"
        "reposicion"     → PoF post-intervención = 0 para cada componente
                           (activo nuevo) ⇒ EAL evitada = PoF_actual × CoF.
        "rehabilitacion" → PoF post = Weibull(min(age_effective_new, edad))
                           por componente; la EAL evitada baja pero no se anula.
    age_effective_new : float
        Edad efectiva única post-rehabilitación (años). Sólo se usa con
        int_type == "rehabilitacion". Se acota por componente a su propia edad
        (una rehabilitación nunca deja el activo "más viejo").

    Devuelve
    --------
    (total_COP, desglose)
        total_COP : float — suma de la EAL evitada de todos los componentes.
        desglose  : list[dict] — un dict por componente con curva:
                    {label, pof_now, pof_new, cof_COP, eal_avoided_COP}.
    """
    total = 0.0
    desglose = []
    for c in (components or []):
        beta = float(getattr(c, "weibull_beta", 0.0) or 0.0)
        eta  = float(getattr(c, "weibull_eta", 0.0) or 0.0)
        age  = float(getattr(c, "asset_age_years", 0.0) or 0.0)
        cof  = float(getattr(c, "weibull_cof_COP", 0.0) or 0.0)
        if not (beta > 0 and eta > 0 and cof > 0):
            continue
        pof_now = _weibull_pof_at_age(beta, eta, age)
        if int_type == "rehabilitacion":
            pof_new = _weibull_pof_at_age(beta, eta, min(age_effective_new, age))
        else:                                    # reposicion (activo nuevo)
            pof_new = 0.0
        eal_avoided = max(pof_now - pof_new, 0.0) * cof
        total += eal_avoided
        desglose.append({
            "label":            getattr(c, "label", "") or "",
            "pof_now":          pof_now,
            "pof_new":          pof_new,
            "cof_COP":          cof,
            "eal_avoided_COP":  eal_avoided,
        })
    return total, desglose


def _build_extra_component_risk_input(base_comp, methodology: str,
                                       residual_cons_declared,
                                       residual_prob_declared):
    """Construye el RiskInput de un componente 'extra' (índice 1+) de FASE B
    Bloque 2b para una alternativa.

    Bugfix (code review, hallazgo de doble conteo): en asset_aware, un
    componente adicional con curva Weibull propia (weibull_beta > 0) que
    ADEMÁS declara aquí un residual matricial distinto del baseline hace que
    el motor calcule su propio ΔR (rama Weibull de _calc_risk) — que se SUMA
    al beneficio financiero que ese mismo componente ya aporta por el driver
    EAL-evitada (_asset_aware_eal_avoided_COP(_base_components, ...)).
    Se neutraliza exactamente igual que el componente principal (índice 0):
    residual == baseline tanto en el escalar como en el desglose por tipo,
    para que el ΔR propio del motor sea 0 y el único canal de beneficio
    financiero sea el driver. El valor declarado por el analista se preserva
    en session_state por si se necesita para display; esta función sólo
    decide qué se envía al motor (D-14, hallazgo de code review).

    Para cualquier otro caso (componente driver_based, o metodología distinta
    de asset_aware), el residual declarado por el analista pasa sin
    modificar — no hay canal EAL-evitada paralelo que justifique
    neutralizarlo.
    """
    _is_weibull_extra = (methodology == "asset_aware" and base_comp.weibull_beta > 0)
    if _is_weibull_extra:
        _rc_eng = base_comp.baseline_consequence_level
        _rp_eng = base_comp.baseline_probability_level
        _res_ctypes = dict(base_comp.consequence_types)
        _res_ctype_probs = dict(base_comp.consequence_type_probs)
    else:
        _rc_eng = residual_cons_declared
        _rp_eng = residual_prob_declared
        _res_ctypes = {}
        _res_ctype_probs = {}

    return RiskInput(
        baseline_consequence_level=base_comp.baseline_consequence_level,
        baseline_probability_level=base_comp.baseline_probability_level,
        residual_consequence_level=_rc_eng,
        residual_probability_level=_rp_eng,
        consequence_types=base_comp.consequence_types,
        residual_consequence_types=_res_ctypes,
        consequence_type_probs=base_comp.consequence_type_probs,
        residual_consequence_type_probs=_res_ctype_probs,
        weibull_beta=base_comp.weibull_beta,
        weibull_eta=base_comp.weibull_eta,
        asset_age_years=base_comp.asset_age_years,
        label=base_comp.label,
        asset_type=base_comp.asset_type,
        source_asset_id=base_comp.source_asset_id,
    )


def _build_risk_matrix_fig(
    bl_types:  dict,          # {tipo: cons_level 1-5}  baseline
    bl_probs:  dict,          # {tipo: prob_level 1-5}  baseline
    ctm_config: dict = None,  # consequence_types_matrix config
    title: str = "Matriz de Riesgo 5×5",
    alt_data: list = None,    # [{label, color, res_types, res_probs}]
) -> "go.Figure":
    """Renderiza una matriz de riesgo 5×5 (Copperleaf C55 Brecha A).

    Fondo heatmap con zonas Verde/Ámbar/Rojo según score = Consecuencia × Probabilidad.
    Un marcador por tipo de consecuencia en la posición baseline.
    Flechas + marcadores residuales para cada alternativa (alt_data).
    """
    _x_lbls = ["P1-Rara", "P2-Baja", "P3-Posible", "P4-Prob.", "P5-Seguro"]
    _y_lbls = ["L1", "L2", "L3", "L4", "L5"]  # bottom→top en Plotly

    # Background heatmap: score = (row+1)*(col+1), row=0→L1, col=0→P1
    # Invertimos filas para que L5 aparezca arriba (y-axis autorange)
    _z_bg = [[(r + 1) * (c + 1) for c in range(5)] for r in range(5)]
    # colorscale: 1-5 verde, 6-12 ámbar, 13-25 rojo
    _cs = [
        [0.00, "rgba(34,197,94,0.30)"],
        [0.20, "rgba(34,197,94,0.30)"],   # score 5 → 5/25=0.20
        [0.20, "rgba(251,191,36,0.35)"],
        [0.48, "rgba(251,191,36,0.35)"],  # score 12 → 12/25=0.48
        [0.48, "rgba(220,38,38,0.40)"],
        [1.00, "rgba(220,38,38,0.40)"],
    ]
    _fig_rm = go.Figure()
    _fig_rm.add_trace(go.Heatmap(
        z=_z_bg,
        x=_x_lbls, y=_y_lbls,
        colorscale=_cs, showscale=False, zmin=1, zmax=25,
        hovertemplate="Score: %{z}<extra></extra>",
    ))

    # Type palette
    _tcfg = {
        "financiero":   ("#1E40AF", "Fin."),
        "hs":           ("#DC2626", "H&S"),
        "ambiental":    ("#15803D", "Amb."),
        "regulatorio":  ("#7C3AED", "Reg."),
        "reputacional": ("#D97706", "Rep."),
    }
    def _xy(c_lv, p_lv):
        """Convierte nivel 1-5 a label de eje."""
        return _x_lbls[max(0, min(4, p_lv - 1))], _y_lbls[max(0, min(4, c_lv - 1))]

    # Baseline dots (one per type)
    for typ, c_lv in bl_types.items():
        p_lv = bl_probs.get(typ, 3)
        _col, _abbr = _tcfg.get(typ, ("#94A3B8", typ[:3].title()))
        _lbl = (ctm_config or {}).get(typ, {}).get("label", typ.capitalize())
        _xv, _yv = _xy(c_lv, p_lv)
        _fig_rm.add_trace(go.Scatter(
            x=[_xv], y=[_yv], mode="markers+text",
            marker=dict(size=18, color=_col, symbol="circle",
                        line=dict(width=2, color="white")),
            text=[_abbr], textfont=dict(color="white", size=9),
            textposition="middle center",
            name=f"{_lbl} (base)",
            hovertemplate=f"<b>{_lbl} — Baseline</b><br>C{c_lv}×P{p_lv}={c_lv*p_lv} pts<extra></extra>",
            showlegend=True,
        ))

    # Alternative residual: arrows + residual dots
    _alt_symbols = ["square", "diamond", "triangle-up", "star"]
    for _ai, _ad in enumerate(alt_data or []):
        _a_res_t = _ad.get("res_types", {})
        _a_res_p = _ad.get("res_probs", {})
        _a_lbl   = _ad.get("label", f"Alt {chr(65+_ai)}")
        _a_col   = _ad.get("color", EPM_DARK)
        _sym     = _alt_symbols[_ai % len(_alt_symbols)]
        for typ, c_res in _a_res_t.items():
            p_res = _a_res_p.get(typ, 3)
            c_bl  = bl_types.get(typ, c_res)
            p_bl  = bl_probs.get(typ, p_res)
            _lbl  = (ctm_config or {}).get(typ, {}).get("label", typ.capitalize())
            _xbl, _ybl = _xy(c_bl, p_bl)
            _xrs, _yrs = _xy(c_res, p_res)
            if (_xbl != _xrs) or (_ybl != _yrs):
                _fig_rm.add_annotation(
                    x=_xrs, y=_yrs, ax=_xbl, ay=_ybl,
                    axref="x", ayref="y",
                    arrowhead=2, arrowsize=1.2, arrowwidth=2,
                    arrowcolor=_a_col, showarrow=True,
                )
            _fig_rm.add_trace(go.Scatter(
                x=[_xrs], y=[_yrs], mode="markers",
                marker=dict(size=14, color=_a_col, symbol=_sym,
                            line=dict(width=2, color="white")),
                name=f"{_a_lbl} – {_lbl[:8]}",
                hovertemplate=(f"<b>{_a_lbl} · {_lbl}</b><br>C{c_res}×P{p_res}="
                               f"{c_res*p_res} pts<extra></extra>"),
                showlegend=True,
            ))

    _fig_rm.update_layout(
        title=dict(text=title, font=dict(size=12, color="#1E3A5F", family="Inter,Arial")),
        height=320,
        margin=dict(t=40, b=60, l=50, r=10),
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis=dict(title="Probabilidad de Falla", gridcolor="#E2E8F0",
                   categoryorder="array", categoryarray=_x_lbls),
        yaxis=dict(title="Consecuencia", gridcolor="#E2E8F0",
                   categoryorder="array", categoryarray=_y_lbls),
        legend=dict(orientation="h", yanchor="top", y=-0.18,
                    xanchor="center", x=0.5,
                    font=dict(size=9, family="Inter,Arial")),
    )
    return _fig_rm


def metric_card(label: str, value: str, sub: str = "",
                color: str = EPM_GREEN) -> None:
    """KPI card estilo Copperleaf — tile con borde superior de color."""
    color_map = {
        EPM_GREEN:"", EPM_DARK:"", EPM_AMBER:"amber",
        EPM_RED:"red", EPM_TEAL:"teal", EPM_BLUE:"blue", EPM_GRAY:"gray",
    }
    cls = color_map.get(color, "")
    st.markdown(
        f'<div class="kpi-card {cls}" style="border-top-color:{color}">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        f'<div class="kpi-sub">{sub}</div></div>',
        unsafe_allow_html=True
    )

def page_header(title: str, subtitle: str = "", badge: str = "") -> None:
    """Header de página con banda verde EPM estilo C55."""
    bdg = f'<span class="page-badge">{badge}</span>' if badge else ""
    st.markdown(
        f'<div class="epm-page-header">'
        f'<div><div class="page-title">{title}</div>'
        f'<div class="page-sub">{subtitle}</div></div>'
        f'{bdg}</div>',
        unsafe_allow_html=True
    )

def section_bar(title: str) -> None:
    """Separador de sección estilo C55."""
    st.markdown(f'<div class="section-title">{title}</div>',
                unsafe_allow_html=True)

def status_box(text: str, kind: str = "ok") -> None:
    """Box contextual (ok/warn/err/info)."""
    st.markdown(f'<div class="box-{kind}">{text}</div>',
                unsafe_allow_html=True)

def inline_tag(text: str, color: str = "green") -> str:
    """HTML de tag inline."""
    return f'<span class="tag tag-{color}">{text}</span>'

# ══════════════════════════════════════════════════════════════════════════════
# REGISTRO DE ACTIVOS — vista de administración standalone (D-14, Task 16)
# ══════════════════════════════════════════════════════════════════════════════
def render_registro_activos():
    """Administración del Registro de Activos: alta manual, importación
    masiva CSV/Excel y listado/búsqueda con retiro (soft-delete).

    Independiente del wizard de evaluación de proyectos — el wizard (Tasks
    17-19) tendrá su propio flujo de búsqueda/alta de activos "ahí mismo"
    dentro de Paso 2; esta página es la vista de administración dedicada."""
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
            _new_eta = st.number_input("η (años)", min_value=1.0, max_value=300.0, value=float(_wbd_ra.get("eta", 30.0)), step=1.0)
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
        _archivo = st.file_uploader("Archivo CSV o Excel", type=["csv", "xlsx", "xls"], key="ra_importar_file")
        if _archivo is not None and st.button("Importar", key="ra_importar_btn"):
            try:
                _importados, _errores = _registry.import_bytes(_archivo.getvalue(), _archivo.name)
                st.success(f"{len(_importados)} activos importados.")
                if _errores:
                    st.warning(f"{len(_errores)} filas con error:")
                    st.dataframe(pd.DataFrame(_errores), use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"Error al cargar: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    # ══════════════════════════════════════════════════════════════════════
    # RAIL — LOGO EPM (marca corporativa, siempre visible)
    # ══════════════════════════════════════════════════════════════════════
    cfg = get_config()
    meta = cfg.get("_metadata", {})
    _wacc_cfg  = cfg.get("wacc", 0.10)
    _hz_cfg    = cfg.get("analysis_horizon_years", 10)
    _has_ahbu  = bool(cfg.get("asset_horizons_by_bu"))
    _hz_label  = f"{_hz_cfg}a*" if _has_ahbu else f"{_hz_cfg}a"
    _all_ok    = PULP_OK and SCIPY_OK
    _eng_dot   = "ok" if _all_ok else "warn"
    st.markdown(f"""
    <div class="epm-sidebar-logo">
        <div style="display:flex;align-items:center;gap:8px">
            <div class="logo-bolt">⚡</div>
            <div class="logo-main">EPM</div>
        </div>
        <div class="logo-sub">Asset Investment Planning</div>
        <div class="logo-version">
            <span class="rail-sys-dot {_eng_dot}"></span>
            v{meta.get('version','3.6')} &nbsp;·&nbsp; WACC {_wacc_cfg*100:.0f}% &nbsp;·&nbsp; {_hz_label}
        </div>
    </div>
    """, unsafe_allow_html=True)
    if _has_ahbu:
        st.caption("* Horizonte por tipo de activo activo (IEEE/IEC/CREG)")

    # ══════════════════════════════════════════════════════════════════════
    # RAIL — CARGA DE PORTAFOLIO  (función persistente clave)
    # ══════════════════════════════════════════════════════════════════════
    st.markdown(
        "<span class='rail-section-label'>📂 Portafolio</span>"
        "<span style='font-size:0.58rem;color:#4ADE80;margin-left:6px'>"
        "— el Wizard funciona sin archivo</span>",
        unsafe_allow_html=True)

    uploaded = st.file_uploader(
        "Excel · CSV",
        type=["xlsx","xls","csv"],
        key="uploader",
        label_visibility="collapsed",
    )
    if uploaded and uploaded.name != st.session_state.get("last_upload"):
        with st.spinner("Procesando..."):
            try:
                loader = PortfolioLoader(cfg)
                projects, report = loader.from_bytes(uploaded.read(), uploaded.name)
                ev = loader.enrich_with_vnt(projects)
                st.session_state["projects"]         = projects
                st.session_state["eval_results"]     = ev
                st.session_state["portfolio_report"] = report
                st.session_state["opt_result"]       = None
                st.session_state["last_upload"]      = uploaded.name
            except Exception as e:
                st.error(f"Error al cargar: {e}")

    # Estado del portafolio — compacto
    n_proj = len(st.session_state["projects"])
    rep    = st.session_state.get("portfolio_report", {})
    n_warn = len(rep.get("warnings", []))

    if n_proj:
        ev_all      = st.session_state.get("eval_results", [])
        total_capex = sum(r.get("capex_COP",0) for r in ev_all if "error" not in r)
        total_vnt   = sum(r.get("net_value_COP",0) for r in ev_all if "error" not in r)
        n_pos       = sum(1 for r in ev_all if r.get("net_value_COP",0) > 0)
        _fname      = (st.session_state.get("last_upload") or "Portafolio")[:24]
        _warn_html  = (f'<div style="font-size:0.63rem;color:#FCD34D;margin-top:3px">'
                       f'⚠ {n_warn} advertencia(s)</div>' if n_warn else "")
        st.markdown(f"""
        <div class="rail-portfolio-card">
            <div class="rpc-title">{_fname}</div>
            <div class="rpc-count">{n_proj} proyectos</div>
            <div class="rpc-meta">
                {n_pos} VNT+ &nbsp;·&nbsp;
                CAPEX ${total_capex/1e9:.1f}B<br>
                VNT ${total_vnt/1e9:.2f}B
            </div>
            {_warn_html}
        </div>""", unsafe_allow_html=True)
    else:
        _n_duelos_sb = len(st.session_state.get("wizard_results") or [])
        if _n_duelos_sb:
            st.markdown(
                f'<div style="background:rgba(0,168,107,0.15);border:1px solid rgba(0,168,107,0.3);'
                f'border-radius:6px;padding:8px 10px;margin:6px 0;font-size:0.7rem;color:#6EE7B7">'
                f'✅ {_n_duelos_sb} evaluación(es) desde Wizard</div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                '<div style="font-size:0.68rem;color:#4ADE80;text-align:center;'
                'padding:10px 8px;border:1px dashed rgba(0,168,107,0.4);'
                'border-radius:6px;margin:8px 0;line-height:1.6">'
                'Sin portafolio cargado<br>'
                '<span style="color:#6EE7B7">El Wizard no lo requiere</span></div>',
                unsafe_allow_html=True)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # RAIL — ÚLTIMO PLAN DE OPTIMIZACIÓN (solo si existe)
    # ══════════════════════════════════════════════════════════════════════
    opt_r = st.session_state.get("opt_result")
    if opt_r:
        st.markdown("<span class='rail-section-label'>📊 Último Plan MILP</span>",
                    unsafe_allow_html=True)
        _s_color = "#6EE7B7" if opt_r.solver_status == "OPTIMAL" else "#FCD34D"
        st.markdown(f"""
        <div class="rail-portfolio-card">
            <div class="rpc-title">{opt_r.solver_status} · {opt_r.solve_time_s:.0f}s</div>
            <div class="rpc-count">{len(opt_r.selected)} proyectos</div>
            <div class="rpc-meta">
                VNT ${opt_r.total_net_value/1e9:.2f}B<br>
                CAPEX ${opt_r.total_capex/1e9:.2f}B
            </div>
        </div>""", unsafe_allow_html=True)
        st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # RAIL — MODO EXECUTIVE / EXPERT (toggle global)
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("<span class='rail-section-label'>🎛 Modo de Vista</span>",
                unsafe_allow_html=True)
    _exec_mode = st.toggle(
        "Modo Executive",
        value=st.session_state.get("executive_mode", False),
        key="sb_exec_toggle",
        help="Executive: oculta fórmulas y logs técnicos, muestra solo narrativa y gráficos. "
             "Expert: vista completa con parámetros Weibull, FVP y logs.",
    )
    st.session_state["executive_mode"] = _exec_mode
    # Sincronizar expert_mode legacy (inverso del executive)
    st.session_state["expert_mode"] = not _exec_mode

    if _exec_mode:
        st.markdown(
            '<div style="background:linear-gradient(135deg,rgba(30,64,175,0.25),rgba(124,58,237,0.18));'
            'border:1px solid rgba(124,58,237,0.4);border-radius:8px;padding:8px 10px;'
            'font-size:0.65rem;color:#C4B5FD;line-height:1.5">'
            '👔 <b>Executive Mode</b><br>'
            'Narrativa de valor · Cascadas · Radar<br>'
            'Sin fórmulas técnicas</div>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="background:rgba(0,168,107,0.12);border:1px solid rgba(0,168,107,0.3);'
            'border-radius:8px;padding:8px 10px;font-size:0.65rem;color:#6EE7B7;line-height:1.5">'
            '🔬 <b>Expert Mode</b><br>'
            'Weibull · FVP · MILP logs<br>'
            'Parámetros técnicos completos</div>',
            unsafe_allow_html=True)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # RAIL — FILTRO DE NEGOCIO (Cockpit Selector)
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("<span class='rail-section-label'>🏭 Negocio Activo</span>",
                unsafe_allow_html=True)
    _bu_options = ["Todos", "TyD", "AyA", "Gas", "Generacion"]
    _bu_icons   = {"Todos": "🌐", "TyD": "⚡", "AyA": "💧", "Gas": "🔥", "Generacion": "🏭"}
    _selected_bu = st.session_state.get("sidebar_bu_filter", "Todos")
    for _bu_opt in _bu_options:
        _is_active = (_selected_bu == _bu_opt)
        _btn_style = (
            "background:rgba(0,168,107,0.25);border:1px solid #00A86B;color:#6EE7B7;"
            if _is_active else
            "background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);color:#94A3B8;"
        )
        if st.button(
            f"{_bu_icons.get(_bu_opt,'●')} {_bu_opt}",
            key=f"sb_bu_{_bu_opt}",
            use_container_width=True,
        ):
            st.session_state["sidebar_bu_filter"] = _bu_opt
            st.rerun()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # RAIL — IDENTIDAD Y ROL (Brecha E — Option 1: declarativo)
    # v2: reemplazar lookup por config["users"][username]
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("<span class='rail-section-label'>👤 Usuario activo</span>",
                unsafe_allow_html=True)

    _rol_labels = {
        "analista":       "📋 Analista (Área)",
        "coordinador_ga": "🔍 Coordinador GA (Subgerencia)",
        "comite_ga":      "🏛️ Comité GA (DGA / ASPD)",
        "director":       "⭐ Director Corporativo",
    }
    _current_user = st.session_state.get("current_user", {})

    _wf_name = st.text_input(
        "Nombre",
        value=_current_user.get("name", ""),
        placeholder="Ej: Juan Mejía",
        key="wf_name_input",
        label_visibility="collapsed",
    )
    _wf_role = st.selectbox(
        "Rol",
        options=list(_rol_labels.keys()),
        format_func=lambda r: _rol_labels[r],
        index=list(_rol_labels.keys()).index(_current_user.get("role", "analista")),
        key="wf_role_input",
        label_visibility="collapsed",
    )
    _wf_cycle = st.text_input(
        "Ciclo presupuestal",
        value=_current_user.get("cycle", "PAI-2026"),
        key="wf_cycle_input",
        label_visibility="collapsed",
        placeholder="PAI-2026",
        help="Ciclo activo — se registra en cada transición de workflow.",
    )

    if st.button("✔ Confirmar identidad", key="wf_confirm_btn",
                 use_container_width=True):
        if _wf_name.strip():
            st.session_state["current_user"] = {
                "name":  _wf_name.strip(),
                "role":  _wf_role,
                "bu":    st.session_state.get("sidebar_bu_filter", "Todos"),
                "cycle": _wf_cycle.strip() or "PAI-2026",
            }
            st.success(f"Bienvenido/a, {_wf_name.strip()}")
        else:
            st.warning("Ingresa tu nombre para continuar.")

    # Badge de pendientes
    _cu = st.session_state.get("current_user", {})
    if _cu.get("role") and _cu.get("name"):
        _wf_store: Dict[str, ProjectWorkflow] = st.session_state.get("workflows", {})
        _pending_n = len(WorkflowEngine.pending_for_role(
            _wf_store, _cu["role"], _cu.get("bu", "Todos")))
        if _pending_n > 0:
            st.markdown(
                f'<div style="background:#EF4444;color:white;border-radius:8px;'
                f'padding:4px 10px;text-align:center;font-size:13px;font-weight:700;">'
                f'⚠️ {_pending_n} acción{"es" if _pending_n!=1 else ""} pendiente{"s" if _pending_n!=1 else ""}'
                f'</div>', unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div style="background:#064E3B;color:#6EE7B7;border-radius:8px;'
                f'padding:4px 10px;text-align:center;font-size:12px;">'
                f'✅ Sin pendientes · {_rol_labels.get(_cu["role"],"")}'
                f'</div>', unsafe_allow_html=True)

    st.divider()

    # ══════════════════════════════════════════════════════════════════════
    # RAIL — ESTADO DEL SISTEMA (compacto, sin verbosidad)
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("<span class='rail-section-label'>Sistema</span>",
                unsafe_allow_html=True)
    _milp_dot   = "ok" if PULP_OK else "err"
    _scipy_dot  = "ok" if SCIPY_OK else "warn"
    st.markdown(f"""
    <div style="padding:2px 0">
        <div class="rail-sys-status">
            <div class="rail-sys-dot {_milp_dot}"></div>
            <span>PuLP / CBC — MILP Enterprise</span>
        </div>
        <div class="rail-sys-status">
            <div class="rail-sys-dot {_scipy_dot}"></div>
            <span>scipy — Optimizador legacy</span>
        </div>
        <div class="rail-sys-status">
            <div class="rail-sys-dot ok"></div>
            <span>ISO 55001:2024 §4.5 · SHA-256</span>
        </div>
        <div class="rail-sys-status" style="margin-top:6px;color:#6EE7B7">
            <span>WACC {_wacc_cfg*100:.1f}% · {_hz_cfg}a · FVP {((1-(1+_wacc_cfg)**(-_hz_cfg))/_wacc_cfg):.3f}</span>
        </div>
        <div style="font-size:0.60rem;color:#4ADE80;margin-top:4px">
            → Parámetros en ⚙️ CONFIG
        </div>
    </div>
    """, unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# PESTAÑAS PRINCIPALES — v12.2 (4 pestañas consolidadas)
# ══════════════════════════════════════════════════════════════════════════════
# ── Top Value HUD (Sticky) — 3 KPIs maestros + estado del sistema ────────────
_rb_n_proj   = len(st.session_state.get("projects", []))
_rb_wr       = st.session_state.get("wizard_results") or []
_rb_opt      = st.session_state.get("opt_result")
_rb_cfg_r    = get_config()
_rb_wacc     = _rb_cfg_r.get("wacc", 0.10)
_rb_hz       = _rb_cfg_r.get("analysis_horizon_years", 10)
_rb_ev       = st.session_state.get("eval_results") or []

# --- Calcular los 3 KPIs maestros ---
# KPI 1: VNT Total (portafolio o wizard)
_hud_vnt_cop  = sum(r.get("net_value_COP", 0) for r in _rb_ev if "error" not in r)
if not _hud_vnt_cop and _rb_wr:
    _hud_vnt_cop = sum(
        _mr.get("alternative_result", {}).get("net_value_COP", 0)
        for _mr in _rb_wr if isinstance(_mr, dict)
    )
if _rb_opt:
    _hud_vnt_cop = _rb_opt.total_net_value

# KPI 2: Riesgo Mitigado (ΔR agregado)
_hud_dr_cop = sum(r.get("delta_r_npv_COP", 0) for r in _rb_ev if "error" not in r)
if not _hud_dr_cop and _rb_wr:
    _hud_dr_cop = sum(
        _mr.get("alternative_result", {}).get("delta_r_npv_COP", 0)
        for _mr in _rb_wr if isinstance(_mr, dict)
    )

# KPI 3: VEI — Value Efficiency Index = VNT / CAPEX total
_hud_capex_cop = sum(r.get("capex_COP", 0) for r in _rb_ev if "error" not in r)
if not _hud_capex_cop and _rb_wr:
    _hud_capex_cop = sum(
        _mr.get("alternative_result", {}).get("capex_COP", 0)
        for _mr in _rb_wr if isinstance(_mr, dict)
    )
if _rb_opt:
    _hud_capex_cop = _rb_opt.total_capex
_hud_vei = (_hud_vnt_cop / _hud_capex_cop) if _hud_capex_cop > 0 else 0.0

# Formatos compactos
def _hud_fmt(v: float) -> str:
    mm = v / 1_000_000
    if abs(mm) >= 1_000:
        return f"${mm/1_000:+,.2f}B"
    return f"${mm:+,.1f}MM"

_hud_vnt_str  = _hud_fmt(_hud_vnt_cop) if _hud_vnt_cop != 0 else "—"
_hud_dr_str   = _hud_fmt(_hud_dr_cop)  if _hud_dr_cop  != 0 else "—"
_hud_vei_str  = f"{_hud_vei:.2f}×"     if _hud_vei     != 0 else "—"
_hud_vnt_cls  = "positive" if _hud_vnt_cop >= 0 else "negative"
_hud_dr_cls   = "positive" if _hud_dr_cop  >= 0 else "negative"
_hud_vei_cls  = "positive" if _hud_vei     >= 1 else ("neutral" if _hud_vei > 0 else "neutral")

# Fuentes: motor + datos activos
_pulp_dot_h  = f'<span style="color:{"#4ADE80" if PULP_OK else "#EF4444"}">●</span>'
_scipy_dot_h = f'<span style="color:{"#4ADE80" if SCIPY_OK else "#F59E0B"}">●</span>'
_hud_n_src   = (f"{_rb_n_proj} proy." if _rb_n_proj
                else f"{len(_rb_wr)} duelo(s)" if _rb_wr
                else "Sin datos")
_hud_mode_badge = (
    '<span class="exec-badge">EXECUTIVE</span>'
    if st.session_state.get("executive_mode")
    else '<span class="expert-badge">EXPERT</span>'
)

st.markdown(f"""
<div class="value-hud" style="margin-bottom:0">
  <div class="hud-item" style="border-right:1px solid rgba(0,168,107,0.2);padding-left:0;min-width:160px">
    <span class="hud-label">⚡ EPM · Decisión Estratégica</span>
    <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
      {_hud_mode_badge}
      <span style="font-size:0.62rem;color:#94A3B8;font-family:'JetBrains Mono',monospace">
        WACC {_rb_wacc*100:.0f}% · {_rb_hz}a · {_hud_n_src}
      </span>
    </div>
    <span class="hud-badge" style="margin-top:6px">
      {_pulp_dot_h} PuLP &nbsp; {_scipy_dot_h} scipy &nbsp;·&nbsp; ISO 55001:2024
    </span>
  </div>
  <div class="hud-item">
    <span class="hud-label">VNT Total</span>
    <span class="hud-value {_hud_vnt_cls}" style="background:linear-gradient(135deg,#00A86B,#84CC16);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text">{_hud_vnt_str}</span>
    <span class="hud-badge">Valor Neto Total del portafolio</span>
  </div>
  <div class="hud-item">
    <span class="hud-label">Riesgo Mitigado (ΔR)</span>
    <span class="hud-value {_hud_dr_cls}" style="background:linear-gradient(135deg,#0F766E,#00A86B);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text">{_hud_dr_str}</span>
    <span class="hud-badge">Reducción de pérdida anual esperada (VPN)</span>
  </div>
  <div class="hud-item">
    <span class="hud-label">VEI — Value Efficiency Index</span>
    <span class="hud-value {_hud_vei_cls}" style="background:linear-gradient(135deg,#1E40AF,#7C3AED);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text">{_hud_vei_str}</span>
    <span class="hud-badge {'warn' if _hud_vei < 1 and _hud_vei > 0 else ''}">VNT / CAPEX · {"✓ Eficiente" if _hud_vei >= 1 else ("⚠ Revisar" if _hud_vei > 0 else "Sin evaluaciones")}</span>
  </div>
</div>
""", unsafe_allow_html=True)

TAB_LABELS = [
    "🏠 Dashboard",
    "🔬 Evaluación",
    "📊 Planeador",
    "📋 Auditoría",
    "🗂️ Registro de Activos",
    "⚙️ Config",
]
# ── Auto-switch de pestaña (via JavaScript cuando session state lo pide) ─────
_goto_tab_idx = st.session_state.pop("_goto_tab", None)
if _goto_tab_idx is not None:
    # Inyectar JS que hace click en el botón de la pestaña correcta
    _tab_btn_labels = [lbl.strip() for lbl in TAB_LABELS]
    _target_label   = _tab_btn_labels[_goto_tab_idx] if _goto_tab_idx < len(_tab_btn_labels) else ""
    if _target_label:
        st.markdown(
            f"""<script>
            (function() {{
                const tabs = window.parent.document.querySelectorAll('[data-baseweb="tab"]');
                for (let t of tabs) {{
                    if (t.innerText.trim().includes('{_target_label[:8].strip()}')) {{
                        t.click(); break;
                    }}
                }}
            }})();
            </script>""",
            unsafe_allow_html=True,
        )

tabs = st.tabs(TAB_LABELS)
# Alias semánticos → el código legacy usa los nombres originales de cada bloque
_tab_dash   = tabs[0]   # DASHBOARD  (ex-CONSOLA)
_tab_eval   = tabs[1]   # EVALUACIÓN (ex-PROYECTO tabs[4])
_tab_plan   = tabs[2]   # PLANEADOR  (ex-PORTAFOLIO tabs[1] + OPTIMIZADOR tabs[2])
_tab_audi   = tabs[3]   # AUDITORÍA  (ex-SNAPSHOTS tabs[5] + DEGRADACIÓN tabs[3])
_tab_registro = tabs[4] # REGISTRO DE ACTIVOS — administración standalone (Task 16)
_tab_config = tabs[5]   # CONFIG     — Parámetros del modelo, shadow prices, WACC

# ══════════════════════════════════════════════════════════════════════════════
# TAB DASHBOARD — Consola de Decisión Estratégica  (v9.0 → v13.0)
# ══════════════════════════════════════════════════════════════════════════════
with _tab_dash:
    cfg_c = get_config()
    ev_c  = st.session_state.get("eval_results") or []
    wr_c  = st.session_state.get("wizard_results") or []

    page_header(
        "Consola de Decisión Estratégica",
        "EPM · Asset Investment Planning · ASPD — Sostenibilidad, Planeación y Desempeño",
        "ISO 55001:2024 §4.5 · Copperleaf C55 VDM",
    )

    # ── PANEL DE INICIO RÁPIDO ─────────────────────────────────────────────
    # Nota: Streamlit no permite cambiar de pestaña programáticamente.
    # Los botones aquí CONFIGURAN el estado; el usuario hace clic en la pestaña.
    _wiz_step   = st.session_state.get("wizard_step", 0)
    _wiz_mode   = st.session_state.get("proyecto_mode")
    _wiz_done   = bool(st.session_state.get("wizard_results"))
    _has_active = _wiz_mode is not None and _wiz_step >= 1

    _d1, _d2 = st.columns(2, gap="large")

    with _d1:
        # ── Card: Evaluar un proyecto ──────────────────────────────────────
        _wiz_status = ""
        if _wiz_done:
            _wiz_status = "✅ Último duelo calculado"
        elif _has_active and _wiz_step > 1:
            _step_labels = {1:"Contexto", 2:"Configurar", 3:"Duelo"}
            _wiz_status = f"⏳ En curso — Paso {_wiz_step}: {_step_labels.get(_wiz_step,'')}"
        elif _has_active:
            _wiz_status = "🆕 Listo para comenzar"

        st.markdown(
            f'<div style="background:rgba(0,90,46,0.18);border:1.5px solid rgba(0,168,107,0.45);'
            f'border-left:4px solid #00A86B;border-radius:12px;'
            f'padding:20px 22px;height:100%;backdrop-filter:blur(8px)">'
            f'<div style="font-size:0.65rem;font-weight:700;color:#6EE7B7;text-transform:uppercase;'
            f'letter-spacing:0.10em;margin-bottom:8px">Evaluación de Proyecto</div>'
            f'<div style="font-size:1rem;font-weight:800;color:#D1FAE5;margin-bottom:8px">'
            f'Evaluar un Proyecto</div>'
            f'<div style="font-size:0.82rem;color:#94A3B8;line-height:1.6;margin-bottom:14px">'
            f'Wizard guiado paso a paso: cuantifica los 6 retos, '
            f'corre el motor predictivo y compara alternativas A vs B vs C.</div>'
            f'{"<div style=\"background:rgba(0,168,107,0.15);border:1px solid rgba(0,168,107,0.3);border-radius:6px;padding:5px 10px;" + "font-size:0.78rem;color:#6EE7B7;font-weight:600;margin-bottom:12px\">" + _wiz_status + "</div>" if _wiz_status else ""}'
            f'<div style="background:linear-gradient(135deg,#005C2E,#007A3E);color:#fff;border-radius:8px;padding:9px 14px;'
            f'font-size:0.82rem;font-weight:700;text-align:center;letter-spacing:0.01em">'
            f'→ Haz clic en la pestaña &nbsp;<b>EVALUACIÓN</b>&nbsp; arriba</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.caption(" ")
        # Botón funcional: prepara el wizard y avisa al usuario
        _btn_label = "↺ Reiniciar Wizard de Evaluación" if _has_active else "⚡ Preparar nuevo Wizard"
        if st.button(_btn_label, key="dash_prep_wizard", use_container_width=True):
            st.session_state.update({
                "proyecto_mode":      "local",
                "wizard_step":        1,
                "w_current_alt_idx":  0,
                "w_alts_done":        [],
                "wizard_results":     None,
                "wizard_r_base":      None,
                "wizard_alt_projs":   [],
                "wizard_baseline_proj": None,
            })
            st.success("✅ Wizard reiniciado. Ahora haz clic en la pestaña 🔬 EVALUACIÓN.")

    with _d2:
        # ── Card: Optimizar portafolio ──────────────────────────────────────
        _opt_scen   = st.session_state.get("opt_scenario", "")
        _opt_status = ""
        if _opt_scen == "silo":
            _opt_status = "⚙️ Modo: Silo (por BU)"
        elif _opt_scen == "global":
            _opt_status = "🌐 Modo: Enterprise (consolidado)"

        st.markdown(
            f'<div style="background:rgba(30,58,138,0.18);border:1.5px solid rgba(99,102,241,0.4);'
            f'border-left:4px solid #6366F1;border-radius:12px;'
            f'padding:20px 22px;height:100%;backdrop-filter:blur(8px)">'
            f'<div style="font-size:0.65rem;font-weight:700;color:#A5B4FC;text-transform:uppercase;'
            f'letter-spacing:0.10em;margin-bottom:8px">Optimización de Portafolio</div>'
            f'<div style="font-size:1rem;font-weight:800;color:#E0E7FF;margin-bottom:8px">'
            f'Optimizar Portafolio</div>'
            f'<div style="font-size:0.82rem;color:#94A3B8;line-height:1.6;margin-bottom:14px">'
            f'MILP sobre múltiples proyectos con restricción de presupuesto. '
            f'Silo (por BU) o Enterprise (portafolio consolidado EPM).</div>'
            f'{"<div style=\"background:rgba(99,102,241,0.15);border:1px solid rgba(99,102,241,0.3);border-radius:6px;padding:5px 10px;" + "font-size:0.78rem;color:#A5B4FC;font-weight:600;margin-bottom:12px\">" + _opt_status + "</div>" if _opt_status else ""}'
            f'<div style="background:linear-gradient(135deg,#1E3A8A,#1E40AF);color:#fff;border-radius:8px;padding:9px 14px;'
            f'font-size:0.82rem;font-weight:700;text-align:center;letter-spacing:0.01em">'
            f'→ Haz clic en la pestaña &nbsp;<b>PLANEADOR</b>&nbsp; arriba</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.caption(" ")
        _sc1, _sc2 = st.columns(2)
        with _sc1:
            if st.button("⚙️ Modo Silo", key="dash_silo", use_container_width=True,
                         type="primary" if _opt_scen=="silo" else "secondary"):
                st.session_state["opt_scenario"] = "silo"
                st.success("✅ Modo Silo activo. Haz clic en la pestaña 📊 PLANEADOR.")
        with _sc2:
            if st.button("🌐 Modo Enterprise", key="dash_enterprise", use_container_width=True,
                         type="primary" if _opt_scen=="global" else "secondary"):
                st.session_state["opt_scenario"] = "global"
                st.success("✅ Modo Enterprise activo. Haz clic en la pestaña 📊 PLANEADOR.")

    st.divider()

    # ── 2. KPIs GLOBALES (si hay datos) ───────────────────────────────────
    valid_c = [r for r in ev_c if "error" not in r]
    if valid_c or wr_c:
        section_bar("📊 Estado del Portafolio EPM")
        k_cols = st.columns(5)
        total_capex_c  = sum(r.get("capex_COP",0) for r in valid_c)
        total_vnt_c    = sum(r.get("net_value_COP",0) for r in valid_c)
        n_pos_c        = sum(1 for r in valid_c if r.get("net_value_COP",0) > 0)
        avg_vei_c      = (sum(r.get("vei", r.get("vei_eav", 0)) for r in valid_c
                           if r.get("vei", r.get("vei_eav", 0)) > 0)
                          / max(1, n_pos_c)) if n_pos_c else 0
        n_duelos       = len(wr_c)

        with k_cols[0]: metric_card("Proyectos", str(len(valid_c)),
                                     f"{n_pos_c} VNT+" if valid_c else "Sin portafolio")
        with k_cols[1]: metric_card("CAPEX Total", fmt_cop(total_capex_c) if valid_c else "—",
                                     "2026-2030", EPM_TEAL)
        with k_cols[2]: metric_card("VNT Portafolio", fmt_cop(total_vnt_c) if valid_c else "—",
                                     "Valor Neto Total",
                                     EPM_GREEN if total_vnt_c >= 0 else EPM_RED)
        with k_cols[3]: metric_card("VEI Promedio", f"{avg_vei_c:.2f}×" if valid_c else "—",
                                     "Eficiencia media")
        with k_cols[4]: metric_card("Duelos de Valor", str(n_duelos),
                                     "Evaluaciones desde Wizard", EPM_AMBER)

    # ── 3. DASHBOARDS ESTRATÉGICOS ────────────────────────────────────────
    # Determinar fuente de datos: portafolio cargado > wizard_results
    radar_data  = {}   # {vd_label: valor_MM}
    wfall_data  = {}   # {vd_label: valor_MM}
    tree_rows   = []   # [{label, bu, capex, reto}]

    VD_LABELS = {
        "VD-1": "🔵 Calidad",
        "VD-2": "🟢 Svcs. Eficientes",
        "VD-3": "🟡 Cobertura Univ.",
        "VD-4": "🌿 Carbono Neutral.",
        "VD-5": "💰 Gen. de Valor",
        "VD-6": "📣 Reputación",
    }

    if valid_c:
        for r in valid_c:
            for dr in r.get("driver_results", []):
                # NOTA: la clave del driver es "id" (ver epm_core._calc_driver),
                # no "driver_id" — con la clave equivocada este bloque quedaba
                # agrupando todo bajo "" y el Radar/Waterfall/Treemap de Retos
                # nunca mostraba nada. Corregido de paso al añadir VD-6.
                vd_id  = dr.get("id", "")
                lbl    = VD_LABELS.get(vd_id, vd_id)
                val_mm = dr.get("value_COP", 0) / 1e6
                radar_data[lbl] = radar_data.get(lbl, 0) + val_mm
                wfall_data[lbl] = wfall_data.get(lbl, 0) + val_mm
            # Treemap: predominant reto
            dr_list = r.get("driver_results", [])
            if dr_list:
                top_dr  = max(dr_list, key=lambda d: d.get("value_COP", 0))
                top_vd  = VD_LABELS.get(top_dr.get("id",""), "?")
            else:
                top_vd = "Sin datos"
            tree_rows.append({
                "label": r.get("project_name","?")[:30],
                "bu":    r.get("business_unit","?"),
                "capex": r.get("capex_COP",0) / 1e6,
                "reto":  top_vd,
            })
    elif wr_c:
        # Datos del último Duelo de Valor
        for mr in wr_c:
            ar = mr.get("alternative_result", {})
            for dr in ar.get("driver_results", []):
                vd_id  = dr.get("id", "")
                lbl    = VD_LABELS.get(vd_id, vd_id)
                val_mm = dr.get("value_COP", 0) / 1e6
                radar_data[lbl] = radar_data.get(lbl, 0) + val_mm
                wfall_data[lbl] = wfall_data.get(lbl, 0) + val_mm
            tree_rows.append({
                "label": ar.get("project_name","Alt")[:30],
                "bu":    ar.get("business_unit","?"),
                "capex": ar.get("capex_COP",0) / 1e6,
                "reto":  "Desde Wizard",
            })

    has_viz = bool(radar_data)

    if has_viz:
        dash_col1, dash_col2 = st.columns([1, 1], gap="large")

        # ── Radar de Alineación Estratégica ──────────────────────────────
        with dash_col1:
            section_bar("🕸️ Radar de Alineación — 6 Retos EPM")
            all_vd_labels = list(VD_LABELS.values())
            radar_vals    = [max(0, radar_data.get(lbl, 0)) for lbl in all_vd_labels]
            radar_max     = max(radar_vals) if any(v > 0 for v in radar_vals) else 1
            radar_norm    = [v / radar_max * 100 for v in radar_vals]
            # Cerrar el polígono
            theta = all_vd_labels + [all_vd_labels[0]]
            r_raw = radar_vals + [radar_vals[0]]
            r_norm = radar_norm + [radar_norm[0]]

            fig_radar = go.Figure()
            fig_radar.add_trace(go.Scatterpolar(
                r=r_norm, theta=theta,
                fill="toself",
                fillcolor="rgba(0,122,62,0.12)",
                line=dict(color=EPM_GREEN, width=2.5),
                name="Valor por Reto",
                hovertemplate="%{theta}: $%{customdata:,.0f} MM<extra></extra>",
                customdata=r_raw,
            ))
            fig_radar.update_layout(
                polar=dict(
                    radialaxis=dict(visible=True, range=[0, 100],
                                    ticksuffix="%", tickfont=dict(size=9)),
                    angularaxis=dict(tickfont=dict(size=11, color="#005C2E",
                                                    family="IBM Plex Sans")),
                ),
                showlegend=False,
                height=360,
                margin=dict(t=20, b=20, l=50, r=50),
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_radar, use_container_width=True)
            st.caption(
                "Normalizado al reto de mayor valor (100%). "
                "La superficie expresa la diversidad estratégica del portafolio.")

        # ── Waterfall de Beneficios por Reto ─────────────────────────────
        with dash_col2:
            section_bar("📊 Waterfall de Beneficios — VNT por Reto")
            if wfall_data:
                wf_labels = list(wfall_data.keys())
                wf_vals   = [wfall_data[k] for k in wf_labels]
                wf_total  = sum(wf_vals)

                # Construir waterfall: inicio → cada VD → total
                wf_measure = ["relative"] * len(wf_labels) + ["total"]
                wf_x       = wf_labels + ["VNT Total"]
                wf_y       = wf_vals + [wf_total]
                wf_colors  = [
                    EPM_GREEN if v >= 0 else EPM_RED for v in wf_vals
                ] + [EPM_TEAL]

                fig_wf = go.Figure(go.Waterfall(
                    orientation="v",
                    measure=wf_measure,
                    x=wf_x,
                    y=wf_y,
                    texttemplate="$%{y:,.0f}MM",
                    textposition="outside",
                    connector=dict(line=dict(color="#CBD5E1", width=1)),
                    increasing=dict(marker_color=EPM_GREEN),
                    decreasing=dict(marker_color=EPM_RED),
                    totals=dict(marker_color=EPM_TEAL),
                ))
                fig_wf.update_layout(
                    height=360,
                    yaxis_title="VNT (MM COP)",
                    margin=dict(t=20, b=60, l=40, r=20),
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(family="IBM Plex Sans, sans-serif", size=11),
                    showlegend=False,
                )
                st.plotly_chart(fig_wf, use_container_width=True)

        # ── Treemap de Valor: CAPEX vs Reto predominante ─────────────────
        if tree_rows:
            section_bar("🗺️ Treemap — CAPEX vs. Reto Estratégico Predominante")

            # Paleta de retos
            RETO_COLORS = {
                "🔵 Calidad":           "#1E40AF",
                "🟢 Svcs. Eficientes":  "#007A3E",
                "🟡 Cobertura Univ.":   "#D97706",
                "🌿 Carbono Neutral.":  "#0F766E",
                "💰 Gen. de Valor":     "#7C3AED",
                "Desde Wizard":         "#94A3B8",
                "Sin datos":            "#CBD5E1",
            }
            tm_labels  = [r["label"] for r in tree_rows]
            tm_parents = [f'{r["bu"]} · {r["reto"]}' for r in tree_rows]
            tm_values  = [max(0.1, r["capex"]) for r in tree_rows]
            tm_colors  = [RETO_COLORS.get(r["reto"], "#94A3B8") for r in tree_rows]

            fig_tm2 = go.Figure(go.Treemap(
                labels=tm_labels,
                parents=tm_parents,
                values=tm_values,
                texttemplate="<b>%{label}</b><br>$%{value:.0f}MM CAPEX",
                hovertemplate=(
                    "<b>%{label}</b><br>"
                    "CAPEX: $%{value:.0f} MM<br>"
                    "Reto: %{parent}<extra></extra>"
                ),
                marker=dict(colors=tm_colors, line=dict(width=1.5, color="white")),
                tiling=dict(packing="squarify"),
            ))
            fig_tm2.update_layout(
                height=380,
                margin=dict(t=10, l=0, r=0, b=0),
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(family="IBM Plex Sans, sans-serif", size=11),
            )
            st.plotly_chart(fig_tm2, use_container_width=True)

            # Leyenda de retos
            leg_cols = st.columns(len(RETO_COLORS))
            for lc, (reto, color) in zip(leg_cols, RETO_COLORS.items()):
                with lc:
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:6px;'
                        f'font-size:0.7rem;color:#374151">'
                        f'<span style="width:10px;height:10px;border-radius:2px;'
                        f'background:{color};display:inline-block"></span>'
                        f'{reto}</div>',
                        unsafe_allow_html=True,
                    )

    else:
        # Estado vacío — instrucciones de arranque
        st.markdown("""
<div style="text-align:center;padding:40px 20px;background:#F8FAFC;
     border:1px dashed #CBD5E1;border-radius:10px;margin:20px 0">
  <div style="font-size:2rem;margin-bottom:12px">📈</div>
  <div style="font-size:0.95rem;font-weight:600;color:#1E293B;margin-bottom:8px">
    Los dashboards aparecerán aquí automáticamente</div>
  <div style="font-size:0.8rem;color:#94A3B8;max-width:480px;margin:0 auto;line-height:1.6">
    Tienes dos caminos:<br>
    <b>① Wizard sin archivos</b>: Pestaña 🔬 PROYECTO → evalúa alternativas sin cargar Excel.<br>
    <b>② Portafolio Excel/CSV</b>: Arrastra tu archivo en el panel lateral → los gráficos
    se generan automáticamente.
  </div>
</div>""", unsafe_allow_html=True)

    # (navegación: usuario hace clic en la pestaña directamente)

# ══════════════════════════════════════════════════════════════════════════════
# TAB EVALUACIÓN — Wizard Duelo de Alternativas v17.0  (UX redesign)
# ══════════════════════════════════════════════════════════════════════════════
with _tab_eval:
    cfg_e = get_config()
    # ── epm_config: config_v3 + fallback DEFAULT_CONFIG para consequence_types_matrix ──
    # config.json solo tiene consequence_levels y probability_levels (tabla genérica).
    # DEFAULT_CONFIG (epm_core) tiene consequence_types_matrix completo (5 tipos × BU).
    _rm_v3    = cfg_e.get("risk_matrix", {})
    _rm_merge = dict(_rm_v3)
    if not _rm_merge.get("consequence_types_matrix"):
        _rm_merge["consequence_types_matrix"] = (
            DEFAULT_CONFIG.get("risk_matrix", {}).get("consequence_types_matrix", {})
        )
    if not _rm_merge.get("consequence_levels_by_bu"):
        _rm_merge["consequence_levels_by_bu"] = (
            DEFAULT_CONFIG.get("risk_matrix", {}).get("consequence_levels_by_bu", {})
        )
    epm_config = {**cfg_e, "risk_matrix": _rm_merge}

    # ── Stepper visual 3 pasos — Copperleaf-style ────────────────────────────
    _wz = max(1, st.session_state.get("wizard_step", 1))
    _wz_ui = min(_wz, 3)
    _STEPS_UI = [
        ("1", "Contexto",   "Proyecto · BU · Tipo · Alternativas"),
        ("2", "Configurar", "Retos EPM · Metodología"),
        ("3", "Duelo",      "Do Nothing → Alternativas → Resultados"),
    ]
    # Stepper con conectores (líneas) entre pasos — patrón Copperleaf
    _step_html = (
        '<div style="display:flex;align-items:flex-start;gap:0;margin-bottom:22px;'
        'background:#fff;border:1px solid #E2E8F0;border-radius:10px;'
        'box-shadow:0 1px 4px rgba(0,0,0,0.05);overflow:hidden">'
    )
    for _si, (_sn, _sl, _sd) in enumerate(_STEPS_UI):
        _sactive = (_si + 1) == _wz_ui
        _sdone   = (_si + 1) <  _wz_ui
        if _sactive:
            _sbg  = EPM_DARK;  _sfg = "#FFFFFF"; _snfg = "#FFFFFF"; _sdsfg = "#A7F3D0"
        elif _sdone:
            _sbg  = EPM_GREEN; _sfg = "#FFFFFF"; _snfg = "#FFFFFF"; _sdsfg = "#D1FAE5"
        else:
            _sbg  = "#F8FAFC"; _sfg = "#94A3B8"; _snfg = "#94A3B8"; _sdsfg = "#CBD5E1"
        _sfw        = "700" if _sactive else "500"
        _circ_fill  = _snfg if _sdone else "none"
        _txt_fill   = _sbg  if _sdone else _snfg
        _txt_char   = "✓"  if _sdone else _sn
        _snum = (
            f'<svg width="18" height="18" viewBox="0 0 18 18">'
            f'<circle cx="9" cy="9" r="9" fill="{_circ_fill}" stroke="{_snfg}" stroke-width="1.5"/>'
            f'<text x="9" y="13" text-anchor="middle" font-size="9" '
            f'font-family="Inter,sans-serif" font-weight="700" fill="{_txt_fill}">'
            f'{_txt_char}</text></svg>'
        )
        _border_right = "border-right:1px solid #E2E8F0;" if _si < 2 else ""
        _step_html += (
            f'<div style="flex:1;padding:12px 18px;background:{_sbg};{_border_right}">'
            f'<div style="display:flex;align-items:center;gap:7px;margin-bottom:3px">'
            f'{_snum}'
            f'<span style="font-size:0.82rem;font-weight:{_sfw};color:{_sfg};'
            f'letter-spacing:-0.01em">{_sl}</span></div>'
            f'<div style="font-size:0.65rem;color:{_sdsfg};padding-left:25px">{_sd}</div>'
            f'</div>'
        )
    _step_html += '</div>'
    st.markdown(_step_html, unsafe_allow_html=True)

    # ── Expert Toggle (Progressive Disclosure) ───────────────────────────────
    _exp_col, _exp_rest = st.columns([1, 5])
    with _exp_col:
        _expert_mode = st.toggle(
            "🔬 Vista Experta",
            value=st.session_state.get("expert_mode", False),
            key="expert_toggle",
            help="Muestra parámetros técnicos: Weibull, FVP, constantes financieras, MILP")
        st.session_state["expert_mode"] = _expert_mode

    # ── Contexto strip (pasos 2 y 3) — breadcrumb de sesión ──────────────────
    if _wz >= 2:
        _ctx_bu  = st.session_state.get("w_bu", "")
        _ctx_inv = st.session_state.get("w_inv_type", "")
        _ctx_yr  = st.session_state.get("w_exec_year", "")
        _ctx_n   = st.session_state.get("w_alt_count", 2)
        _ctx_nm  = st.session_state.get("w_proj_name", "")
        _ctx_meth = st.session_state.get("w_methodology", "")
        _meth_badge = {"concept_rating":"🟡 Concept","driver_based":"🟢 Driver-Based","asset_aware":"🔵 Asset-Aware"}.get(_ctx_meth, "")
        st.markdown(
            f'<div style="background:#F0FDF4;border:1px solid #B3EFD6;border-radius:8px;'
            f'padding:7px 16px;margin-bottom:16px;font-size:0.76rem;color:{EPM_DEEP};'
            f'display:flex;align-items:center;gap:20px;flex-wrap:wrap">'
            f'<span style="font-weight:700">📋 {_ctx_nm[:45]}</span>'
            f'<span style="color:{EPM_GRAY}">🏢 {_ctx_bu}</span>'
            f'<span style="color:{EPM_GRAY}">📅 {_ctx_yr}</span>'
            f'<span style="color:{EPM_GRAY}">🔀 {_ctx_n} alt.</span>'
            f'{"<span style=\"color:" + EPM_GRAY + "\">" + _meth_badge + "</span>" if _meth_badge else ""}'
            f'</div>',
            unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # PASO 1 — CONTEXTO DEL PROBLEMA
    # ══════════════════════════════════════════════════════════════════════════
    if _wz == 1:
        # ── Layout 2 paneles: formulario (izq) + guía contextual (der) ──────────
        _f_col, _g_col = st.columns([3, 1], gap="large")

        with _f_col:
            _w_name = st.text_input(
                "Nombre del proyecto *",
                value=st.session_state.get("w_proj_name", ""),
                placeholder="Ej: Reposición transformador 115 kV Sub-Norte",
                key="wi_name")

            _w_prob = st.text_area(
                "¿Qué problema resuelve esta inversión? *",
                value=st.session_state.get("w_problem_desc", ""),
                placeholder="Describe brevemente el problema técnico u operativo que justifica actuar…",
                max_chars=256, height=90, key="wi_prob")
            _char_count = len(_w_prob)
            _char_color = EPM_RED if _char_count > 240 else (EPM_AMBER if _char_count > 200 else "#94A3B8")
            st.markdown(
                f'<div style="text-align:right;font-size:0.68rem;color:{_char_color};'
                f'margin-top:-8px;margin-bottom:4px">{_char_count}/256</div>',
                unsafe_allow_html=True)

            # ── Clasificación: 4 campos en 2 filas, proporciones racionales ────
            st.markdown(
                '<div style="font-size:0.7rem;font-weight:700;color:#94A3B8;'
                'text-transform:uppercase;letter-spacing:0.1em;margin:16px 0 10px 0;'
                'border-top:1px solid #E2E8F0;padding-top:14px">Clasificación</div>',
                unsafe_allow_html=True)

            _bu_keys   = list(BU_DRIVER_CONFIG.keys())
            _bu_labels = [BU_DRIVER_CONFIG[k]["label"] for k in _bu_keys]
            _prev_bu   = st.session_state.get("w_bu", _bu_keys[0])
            _prev_bu_i = _bu_keys.index(_prev_bu) if _prev_bu in _bu_keys else 0

            _inv_keys   = list(INVESTMENT_TYPES.keys())
            _prev_inv   = st.session_state.get("w_inv_type", _inv_keys[0])
            _prev_inv_i = _inv_keys.index(_prev_inv) if _prev_inv in _inv_keys else 0

            _r1a, _r1b = st.columns(2, gap="medium")
            with _r1a:
                _w_bu_i = st.selectbox("Unidad de Negocio",
                    range(len(_bu_keys)), format_func=lambda i: _bu_labels[i],
                    index=_prev_bu_i, key="wi_bu")
                _w_bu = _bu_keys[_w_bu_i]
            with _r1b:
                _w_inv_i = st.selectbox("Tipo de Inversión",
                    range(len(_inv_keys)), format_func=lambda i: _inv_keys[i],
                    index=_prev_inv_i, key="wi_inv")
                _w_inv = _inv_keys[_w_inv_i]

            # ── D-14: Horizonte de análisis — único por proyecto, sin
            # sugerencia precargada. El analista debe declararlo explícitamente;
            # no hay fallback silencioso a ningún horizonte por defecto (mismo
            # principio de "sin default automático" que el resto de este diseño).
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

            _yr_opts = [2025, 2026, 2027, 2028, 2029, 2030]
            _r2a, _r2c = st.columns([2, 1], gap="medium")
            with _r2a:
                _w_year = st.selectbox("Año de ejecución", _yr_opts,
                    index=st.session_state.get("w_exec_year_idx", 1), key="wi_year")
            with _r2c:
                _w_capex_ref = st.number_input(
                    "CAPEX estimado (MM COP)",
                    min_value=0.0, max_value=5_000_000.0,
                    value=float(st.session_state.get("w_capex_ref", 0.0)),
                    step=100.0, key="wi_capex_ref",
                    help="Referencia para determinar el nivel de análisis requerido (proporcionalidad IAM SSG §7)")

            # ── Nivel automático de proporcionalidad ─────────────────────────
            _cfg_s1 = get_config()
            _w_model_level_s1 = _wizard_model_level(
                _w_capex_ref,
                _bu_keys[st.session_state.get("wi_bu", 0)]
                    if "wi_bu" not in st.session_state else _w_bu,
                _cfg_s1)
            _ml_s1 = _ML_META[_w_model_level_s1]

            # ── Número de alternativas ────────────────────────────────────────
            st.markdown(
                '<div style="font-size:0.7rem;font-weight:700;color:#94A3B8;'
                'text-transform:uppercase;letter-spacing:0.1em;margin:16px 0 4px 0;'
                'border-top:1px solid #E2E8F0;padding-top:14px">'
                'Alternativas a comparar vs. Do Nothing</div>',
                unsafe_allow_html=True)

            def _alt_label(n):
                letters = " · ".join(chr(65 + i) for i in range(n))
                return f"{n}  —  {'Solo A' if n == 1 else letters}"

            _prev_alt_n = st.session_state.get("w_alt_count", 2)
            _alt_opts   = [1, 2, 3, 4, 5]
            _alt_idx    = _alt_opts.index(_prev_alt_n) if _prev_alt_n in _alt_opts else 1
            _w_alt_count = st.radio(
                "n_alts", options=_alt_opts, index=_alt_idx,
                format_func=_alt_label, horizontal=True,
                key="wi_n_alts", label_visibility="collapsed")

        # ── Panel guía contextual (derecha) ──────────────────────────────────
        with _g_col:
            _bu_focus = BU_DRIVER_CONFIG.get(_w_bu, {}).get("focus", "")
            _bu_lbl   = BU_DRIVER_CONFIG.get(_w_bu, {}).get("label", _w_bu)
            _sc_count = len(BU_DRIVER_CONFIG.get(_w_bu, {}).get("sub_criteria", {}))
            st.markdown(
                f'<div style="background:#F0FDF4;border:1px solid #B3EFD6;'
                f'border-radius:10px;padding:16px;margin-top:28px">'
                f'<div style="font-size:0.7rem;font-weight:700;color:#005C2E;'
                f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px">'
                f'🏢 {_bu_lbl}</div>'
                f'<div style="font-size:0.78rem;color:#065F46;margin-bottom:10px;'
                f'line-height:1.5">{_bu_focus}</div>'
                f'<div style="font-size:0.68rem;color:#047857;border-top:1px solid #A7F3D0;'
                f'padding-top:8px">{_sc_count} sub-criterios disponibles</div>'
                f'</div>',
                unsafe_allow_html=True)

            # ── Banner de proporcionalidad (reactivo al CAPEX ingresado) ─────
            _thr_cfg   = _cfg_s1.get("proportionality_thresholds_COP", {})
            _bu_thr_s1 = _thr_cfg.get(_w_bu) or _thr_cfg.get("Todos") or {}
            _full_mm   = _bu_thr_s1.get("full_model_COP", 10e9) / 1e6
            _simp_mm   = _bu_thr_s1.get("simplified_model_COP", 1e9) / 1e6
            st.markdown(
                f'<div style="background:{_ml_s1["bg"]};border:1.5px solid {_ml_s1["border"]};'
                f'border-radius:10px;padding:14px;margin-top:12px">'
                f'<div style="font-size:0.68rem;font-weight:700;color:{_ml_s1["color"]};'
                f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">'
                f'{_ml_s1["icon"]} Nivel de análisis requerido</div>'
                f'<div style="font-size:0.88rem;font-weight:800;color:{_ml_s1["color"]};'
                f'margin-bottom:6px">{_w_model_level_s1}</div>'
                f'<div style="font-size:0.72rem;color:#374151;line-height:1.5;margin-bottom:8px">'
                f'{_ml_s1["desc"]}</div>'
                f'<div style="font-size:0.65rem;color:#6B7280;border-top:1px solid {_ml_s1["border"]};'
                f'padding-top:6px">'
                f'👤 Gobernanza: <b>{_ml_s1["gov"]}</b><br>'
                f'📏 Umbrales {_w_bu}: '
                f'FULL ≥ ${_full_mm:,.0f}MM · '
                f'SIMPLIFIED ≥ ${_simp_mm:,.0f}MM</div>'
                f'</div>',
                unsafe_allow_html=True)

            # ── Override de nivel: analista puede subir, nunca bajar ────────
            _ML_ORDER   = ["RULE_BASED", "SIMPLIFIED", "FULL"]
            _ml_min_idx = _ML_ORDER.index(_w_model_level_s1)   # piso por CAPEX
            _ml_opts    = _ML_ORDER[_ml_min_idx:]               # sólo ≥ mínimo

            _ml_labels  = {
                "RULE_BASED": "📋 RULE_BASED — Checklist local",
                "SIMPLIFIED": "📊 SIMPLIFIED — Drivers VNT",
                "FULL":       "🔬 FULL — Análisis completo + sensibilidad",
            }
            # Recuperar selección previa (siempre válida como override)
            _ml_prev = st.session_state.get("w_model_level", _w_model_level_s1)
            _ml_prev_safe = _ml_prev if _ml_prev in _ml_opts else _w_model_level_s1
            _ml_prev_idx  = _ml_opts.index(_ml_prev_safe)

            if len(_ml_opts) > 1:
                # Hay margen para subir → mostrar selector
                st.markdown(
                    f'<div style="font-size:0.68rem;font-weight:700;color:{EPM_SLATE};'
                    f'text-transform:uppercase;letter-spacing:0.08em;margin:10px 0 4px 0">'
                    f'Nivel de análisis a usar</div>',
                    unsafe_allow_html=True)
                st.caption(
                    f"Mínimo requerido por proporcionalidad: **{_w_model_level_s1}**. "
                    f"Puede usar un nivel superior si dispone de mayor información.")
                _ml_selected = st.radio(
                    "nivel_override",
                    options=_ml_opts,
                    index=_ml_prev_idx,
                    format_func=lambda x: _ml_labels[x],
                    key="wi_ml_override",
                    label_visibility="collapsed")
            else:
                # FULL ya es el máximo — no hay override posible
                _ml_selected = _w_model_level_s1
                st.caption(
                    f"Nivel fijo: **{_w_model_level_s1}** "
                    f"(CAPEX supera umbral máximo — no aplica simplificación).")

            # Reemplazar el nivel calculado con la selección del analista
            _w_model_level_s1 = _ml_selected

            # ── Familia de activo + nivel de datos: solo aplica a Reposición
            # de Activos (D-14). Crecimiento/Expansión no tiene un activo
            # físico que envejezca — no hay Weibull ni PoF que declarar.
            if _w_inv == "Reposición de Activos":
                # ── Familia de activo + nivel de datos (Data Maturity C55) ───────
                st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
                st.markdown(
                    f'<div style="font-size:0.70rem;font-weight:700;color:{EPM_SLATE};'
                    f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px">'
                    f'🏗️ Familia de activo</div>',
                    unsafe_allow_html=True)

                # Filtrar familias disponibles para esta BU
                _af_opts = {
                    k: v for k, v in ASSET_FAMILIES.items()
                    if _w_bu in v.get("bus_aplicables", [])
                }
                _af_keys   = list(_af_opts.keys())
                _af_labels = [f'{v["icon"]} {v["label"]}' for v in _af_opts.values()]
                _prev_af   = st.session_state.get("w_asset_family", "otro")
                _af_idx    = _af_keys.index(_prev_af) if _prev_af in _af_keys else (
                             _af_keys.index("otro") if "otro" in _af_keys else 0)
                _af_sel_lbl = st.selectbox(
                    "Familia de activo",
                    options=_af_labels,
                    index=_af_idx,
                    key="wi_asset_family_sel",
                    label_visibility="collapsed",
                    help="Define el techo de precisión (metodología preferida) y los "
                         "parámetros Weibull de referencia.")
                _af_sel_key = _af_keys[_af_labels.index(_af_sel_lbl)]
                _af_meta    = _af_opts[_af_sel_key]
                _af_preferred = _af_meta.get("metodologia_preferred", "driver_based")

                # ── Nivel de datos declarado (Data Maturity) ──────────────────────
                _DATA_HIER = {"asset_aware": 3, "driver_based": 2, "concept_rating": 1}
                _DATA_LABELS = {
                    "asset_aware":    "🔵 Asset-Aware  (Calculado — β/η + historial fallas)",
                    "driver_based":   "🟢 Driver-Based (Estimado — Δ físico × precio)",
                    "concept_rating": "🟡 Concept Rating (Conceptual — escala 0-10 C55)",
                }
                _DATA_DESC = {
                    "asset_aware":    "PoF_Weibull × CoF. Máxima precisión. Requiere β, η y edad del activo.",
                    "driver_based":   "Delta operativo medible. Estándar EPM-AIP.",
                    "concept_rating": "Juicio experto. Para etapas conceptuales sin datos métricos.",
                }
                # Solo mostrar niveles ≤ nivel preferido de la familia
                _ceiling = _DATA_HIER.get(_af_preferred, 2)
                _dl_opts  = [k for k, lv in _DATA_HIER.items() if lv <= _ceiling]
                _prev_dl  = st.session_state.get("w_data_level", _af_preferred)
                _prev_dl  = _prev_dl if _prev_dl in _dl_opts else _dl_opts[0]

                st.markdown(
                    f'<div style="font-size:0.70rem;font-weight:700;color:{EPM_SLATE};'
                    f'text-transform:uppercase;letter-spacing:0.08em;margin:8px 0 4px">'
                    f'📊 Nivel de datos disponibles</div>',
                    unsafe_allow_html=True)
                _dl_sel = st.radio(
                    "Nivel de datos",
                    options=_dl_opts,
                    format_func=lambda k: _DATA_LABELS[k],
                    index=_dl_opts.index(_prev_dl),
                    key="wi_data_level_radio",
                    label_visibility="collapsed")
                st.caption(_DATA_DESC.get(_dl_sel, ""))

                # ── Badge de confianza ────────────────────────────────────────────
                _conf_gap = _ceiling - _DATA_HIER.get(_dl_sel, 2)
                _CONF_META = {
                    0: ("🔵 Alta confianza",    EPM_BLUE,  "#EFF6FF",
                        "Metodología óptima para esta familia de activo."),
                    1: ("🟡 Confianza media",   EPM_AMBER, "#FFFBEB",
                        f"Fallback desde {_DATA_LABELS.get(_af_preferred,'').split('(')[0].strip()}. "
                        f"Quedar registrado en snapshot."),
                    2: ("🔴 Confianza mínima",  EPM_RED,   "#FFF5F5",
                        "Doble fallback. Re-evaluar cuando haya datos de condición disponibles."),
                }
                _cconf, _ccolor, _cbg, _cdesc = _CONF_META.get(_conf_gap, _CONF_META[0])
                st.markdown(
                    f'<div style="background:{_cbg};border:1px solid {_ccolor}44;'
                    f'border-radius:8px;padding:8px 12px;margin-top:6px;'
                    f'font-size:0.72rem;color:{_ccolor};font-weight:600">'
                    f'{_cconf} &nbsp;·&nbsp; <span style="font-weight:400;color:{EPM_SLATE}">'
                    f'{_cdesc}</span></div>',
                    unsafe_allow_html=True)

                # Mostrar defaults Weibull si aplica
                if _dl_sel == "asset_aware" and _af_meta.get("weibull_defaults"):
                    _wbd = _af_meta["weibull_defaults"]
                    st.markdown(
                        f'<div style="background:#F0FDFA;border:1px solid #5EEAD4;'
                        f'border-radius:8px;padding:8px 12px;margin-top:6px;font-size:0.68rem;'
                        f'color:#0F766E">'
                        f'<b>Defaults Weibull ({_wbd["norma"]}):</b> '
                        f'β = {_wbd["beta"]} · η = {_wbd["eta"]} años<br>'
                        f'<span style="color:#374151">{_wbd["nota"]}</span></div>',
                        unsafe_allow_html=True)
            else:
                # Crecimiento/Expansión no tiene un activo físico que envejezca
                # — no aplica Familia de Activo. w_asset_family/w_data_level
                # siguen siendo leídos más abajo (Paso 2/3), así que quedan
                # con un valor válido igual (sin selector Weibull/PoF).
                _af_sel_key = "otro"
                _af_meta    = ASSET_FAMILIES["otro"]
                _dl_sel     = "driver_based"

        # ── Botón y validación lazy (solo tras primer intento) ───────────────
        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

        if st.button("Continuar → Configurar retos y metodología",
                     type="primary", use_container_width=True, key="wi_next1"):
            # Validar solo al hacer clic
            _p1_err = []
            if not _w_name.strip():
                _p1_err.append("Ingresa el nombre del proyecto.")
            if not _w_prob.strip():
                _p1_err.append("Ingresa la descripción del problema.")
            if not _w_horizon or _w_horizon <= 0:
                _p1_err.append("Declara el horizonte de análisis (años) — no tiene valor por defecto.")

            if _p1_err:
                for _e in _p1_err:
                    st.error(f"❌ {_e}")
            else:
                st.session_state.update({
                    "wizard_step":     2,
                    "proyecto_mode":   "local",
                    "w_proj_name":     _w_name.strip(),
                    "w_problem_desc":  _w_prob.strip(),
                    "w_bu":            _w_bu,
                    "w_inv_type":      _w_inv,
                    "w_exec_year":     _w_year,
                    "w_exec_year_idx": _yr_opts.index(_w_year),
                    "w_alt_count":     _w_alt_count,
                    "w_capex_ref":     _w_capex_ref,
                    "w_model_level":   _w_model_level_s1,
                    "w_asset_family":  _af_sel_key,
                    "w_data_level":    _dl_sel,
                    "w_analysis_horizon_years": _w_horizon,
                })
                st.rerun()

    # ══════════════════════════════════════════════════════════════════════════
    # PASO 2 — RETOS ESTRATÉGICOS
    # ══════════════════════════════════════════════════════════════════════════
    elif _wz == 2:
        _w_bu = st.session_state.get("w_bu", "TyD")

        _VD_META = {
            "VD-1": ("🔵 Calidad de los Servicios",        "ENS, SAIDI, disponibilidad de planta",          EPM_BLUE),
            "VD-2": ("🟢 Servicios Eficientes",             "OPEX evitado, pérdidas técnicas, O&M",          EPM_DARK),
            "VD-3": ("🟡 Cobertura Universal Sostenible",   "Nuevas conexiones, expansión de red",           EPM_AMBER),
            "VD-4": ("🌿 Protección Hídrica y Carbono Neutralidad", "CO₂ evitado, multas ANLA, energía limpia", EPM_TEAL),
            "VD-5": ("💰 Generación de Valor",              "Multas CREG evitadas, ingresos habilitados",    "#7C3AED"),
            "VD-6": ("📣 Reputación",                       "NPS, CX Forces, imagen institucional",          "#DB2777"),
        }
        # Disponibilidad de cada VD para esta BU: se determina por "bu_filter"
        # en el sub-criterio — la misma fuente de verdad que usa el wizard CLI
        # (epm_core._build_sc_index / sc_applies_to_bu: sub-criterio aplica si
        # no tiene bu_filter, o si esta BU está en su bu_filter).
        # NO se determina por BU_DRIVER_CONFIG: ese catálogo solo lista
        # sub-criterios driver-based (con precio/unidad) y nunca incluyó los
        # concept_rating (VD-1-F, VD-5-C, VD-6-A). Con la versión anterior,
        # cualquier VD cuyo único sub-criterio fuera concept_rating —como
        # VD-6 (Reputación), que no tiene bu_filter y aplica a las 4 BU—
        # aparecía "no aplica esta BU" en el wizard sin importar la BU elegida.
        _available_vds = set()
        for _vd_cfg_s2 in epm_config.get("value_drivers", []):
            for _sc_cfg_s2 in _vd_cfg_s2.get("sub_criteria", []):
                _sc_bu_f_s2 = _sc_cfg_s2.get("bu_filter", [])
                if not _sc_bu_f_s2 or _w_bu in _sc_bu_f_s2:
                    _available_vds.add(_vd_cfg_s2["id"])
                    break
        _prev_sel_vds  = st.session_state.get("w_selected_vds", list(_available_vds))

        # ── Metodología: viene del nivel de datos declarado en Step 1 ─────────
        # El analista declaró su madurez de datos (Data Maturity Level C55) en
        # Step 1. Aquí solo lo leemos — no se vuelve a preguntar.
        _METH_LABELS = {
            "driver_based":   "🟢 Driver-Based",
            "concept_rating": "🟡 Concept Rating",
            "asset_aware":    "🔵 Asset-Aware",
        }
        _METH_DESC = {
            "driver_based":   "Δ físico × precio regulatorio (XM/CREG). Estándar EPM-AIP.",
            "concept_rating": "Escala cualitativa 0-10 (C55). Para etapas tempranas sin datos métricos.",
            "asset_aware":    "Weibull / EEOL predictivo. PoF × Consecuencia.",
        }
        _meth_sel    = st.session_state.get("w_data_level", "driver_based")
        _af_key_s2   = st.session_state.get("w_asset_family", "otro")
        _af_meta_s2  = ASSET_FAMILIES.get(_af_key_s2, ASSET_FAMILIES["otro"])

        _left_s2, _right_s2 = st.columns([3, 2], gap="large")

        with _left_s2:
            st.markdown("#### Retos estratégicos que impacta esta inversión")
            st.caption("Marca solo los retos que aplican. Esto filtra los drivers en el Paso 3.")
            st.markdown("")
            _new_sel_vds = []
            for _vd_id, (_vd_label, _vd_desc, _vd_color) in _VD_META.items():
                _avail   = _vd_id in _available_vds
                _default = _vd_id in _prev_sel_vds and _avail
                _col_a, _col_b = st.columns([1, 6])
                with _col_a:
                    _checked = st.checkbox("", value=_default,
                                           key=f"wi_vd_{_vd_id}",
                                           disabled=not _avail)
                with _col_b:
                    _dim = "color:#CBD5E1" if not _avail else f"color:{_vd_color}"
                    st.markdown(
                        f'<div style="{_dim};font-size:0.88rem;font-weight:600;'
                        f'padding-top:6px">{_vd_label}</div>'
                        f'<div style="font-size:0.72rem;color:{EPM_GRAY};margin-top:1px">'
                        f'{_vd_desc}{"  · <i>no aplica esta BU</i>" if not _avail else ""}</div>',
                        unsafe_allow_html=True)
                if _avail and _checked:
                    _new_sel_vds.append(_vd_id)

        with _right_s2:
            st.markdown("#### Resumen de valoración")
            st.caption("Familia de activo y nivel de datos declarados en el paso anterior.")
            st.markdown("")

            _meth_badge_color = {
                "driver_based":   EPM_DARK,
                "concept_rating": EPM_AMBER,
                "asset_aware":    EPM_BLUE,
            }.get(_meth_sel, EPM_DARK)
            _meth_badge_bg = {
                "driver_based":   "#F0FDF4",
                "concept_rating": "#FFFBEB",
                "asset_aware":    "#EFF6FF",
            }.get(_meth_sel, "#F0FDF4")

            # Badge de confianza
            _af_pref_s2   = _af_meta_s2.get("metodologia_preferred", "driver_based")
            _DATA_HIER_S2 = {"asset_aware": 3, "driver_based": 2, "concept_rating": 1}
            _gap_s2       = _DATA_HIER_S2.get(_af_pref_s2, 2) - _DATA_HIER_S2.get(_meth_sel, 2)
            _CONF_BADGE   = {
                0: ("🔵 Alta confianza",   EPM_BLUE),
                1: ("🟡 Confianza media",  EPM_AMBER),
                2: ("🔴 Confianza mínima", EPM_RED),
            }
            _cbadge, _cbc = _CONF_BADGE.get(_gap_s2, _CONF_BADGE[0])

            # Modos de falla de la familia (max 3)
            _fail_modes = _af_meta_s2.get("modos_falla", [])[:3]
            _fail_html  = "".join(
                f'<div style="font-size:0.66rem;color:{EPM_GRAY}">▸ {fm}</div>'
                for fm in _fail_modes) if _fail_modes else ""

            st.markdown(
                f'<div style="background:{_meth_badge_bg};border:2px solid {_meth_badge_color};'
                f'border-radius:10px;padding:14px 16px">'
                f'<div style="font-size:0.68rem;font-weight:700;text-transform:uppercase;'
                f'letter-spacing:0.08em;color:{EPM_GRAY};margin-bottom:4px">Familia de activo</div>'
                f'<div style="font-size:0.92rem;font-weight:800;color:{_meth_badge_color};'
                f'margin-bottom:2px">{_af_meta_s2["icon"]} {_af_meta_s2["label"]}</div>'
                f'<div style="font-size:0.68rem;font-weight:700;text-transform:uppercase;'
                f'letter-spacing:0.08em;color:{EPM_GRAY};margin:8px 0 2px">Metodología declarada</div>'
                f'<div style="font-size:0.88rem;font-weight:700;color:{_meth_badge_color};'
                f'margin-bottom:2px">{_METH_LABELS.get(_meth_sel,"")}</div>'
                f'<div style="font-size:0.70rem;color:#374151;margin-bottom:6px">'
                f'{_METH_DESC.get(_meth_sel,"")}</div>'
                f'<div style="background:{_cbc}15;border-radius:6px;padding:4px 8px;'
                f'font-size:0.68rem;font-weight:700;color:{_cbc};margin-bottom:6px">'
                f'{_cbadge}</div>'
                + (f'<div style="border-top:1px solid {_meth_badge_color}33;padding-top:6px">'
                   f'<div style="font-size:0.64rem;font-weight:600;color:{EPM_GRAY};'
                   f'margin-bottom:2px">Modos de falla principales:</div>'
                   f'{_fail_html}</div>' if _fail_html else "")
                + f'</div>',
                unsafe_allow_html=True)

        st.divider()

        if not _new_sel_vds:
            st.warning("⚠️ Selecciona al menos un reto estratégico.")

        _nb2a, _nb2b = st.columns(2)
        with _nb2a:
            if st.button("← Volver", use_container_width=True, key="wi_back2"):
                st.session_state["wizard_step"] = 1
                st.rerun()
        with _nb2b:
            if st.button("Continuar →", type="primary", use_container_width=True,
                         key="wi_next2", disabled=not _new_sel_vds):
                st.session_state.update({
                    "wizard_step":          3,
                    "w_selected_vds":       _new_sel_vds,
                    "w_methodology":        _meth_sel,
                    "w_current_alt_idx":    0,
                    "w_alts_done":          [],
                    "wizard_alt_projs":     [],
                    "wizard_baseline_proj": None,
                    "wizard_results":       None,
                })
                st.rerun()

    # ══════════════════════════════════════════════════════════════════════════
    # PASO 3 — DUELO DE ALTERNATIVAS
    # ══════════════════════════════════════════════════════════════════════════
    elif _wz in (3, 4):
        _w_name    = st.session_state.get("w_proj_name", "Proyecto")
        _w_bu      = st.session_state.get("w_bu", "TyD")
        _w_inv     = st.session_state.get("w_inv_type", "Reposición de Activos")
        _w_capex   = 0.0   # CAPEX se ingresa por alternativa en Fase B
        _w_year    = st.session_state.get("w_exec_year", 2026)
        _w_alt_cnt = st.session_state.get("w_alt_count", 2)
        _w_meth        = st.session_state.get("w_methodology", "driver_based")
        _w_sel_vds     = st.session_state.get("w_selected_vds", [])
        _w_cur_idx     = st.session_state.get("w_current_alt_idx", 0)
        _w_model_level = st.session_state.get("w_model_level", "SIMPLIFIED")  # RULE_BASED | SIMPLIFIED | FULL
        _w_done    = st.session_state.get("w_alts_done", [])
        _w_alt_projs = st.session_state.get("wizard_alt_projs", [])
        _w_base_proj = st.session_state.get("wizard_baseline_proj", None)
        _bu_cfg    = BU_DRIVER_CONFIG.get(_w_bu, {})
        _sc_all    = _bu_cfg.get("sub_criteria", {})
        # Sub-criteria filtrados por retos seleccionados
        _sc_active = {k: v for k, v in _sc_all.items()
                      if v.get("vd_id") in _w_sel_vds}

        _METH_BADGES = {
            "concept_rating": "🟡 Concept Rating",
            "driver_based":   "🟢 Driver-Based",
            "asset_aware":    "🔵 Asset-Aware",
        }
        _badge = _METH_BADGES.get(_w_meth, "")

        # Mini-stepper del duelo (Do Nothing → A → B → … → Resultados)
        _prog_items = (["⚠ Do Nothing"] +
                       [f"Alt {chr(65+i)}" for i in range(_w_alt_cnt)] +
                       ["Resultados"])
        _prog_html = '<div style="display:flex;gap:4px;margin-bottom:16px;flex-wrap:wrap">'
        for _pi, _pl in enumerate(_prog_items):
            if _pi == 0:
                _pdone = _w_base_proj is not None
                _pact  = _w_base_proj is None and st.session_state.get("wizard_results") is None
            elif _pi <= _w_alt_cnt:
                _ai    = _pi - 1
                _pdone = _ai < len(_w_done)
                _pact  = (_ai == _w_cur_idx and _w_base_proj is not None
                          and st.session_state.get("wizard_results") is None)
            else:
                _pdone = st.session_state.get("wizard_results") is not None
                _pact  = False
            _pc = EPM_DARK if _pact else (EPM_GREEN if _pdone else "#94A3B8")
            _pb = EPM_LIGHT if (_pact or _pdone) else "#F8FAFC"
            _pf = "700" if _pact else "400"
            _prog_html += (
                f'<div style="padding:4px 12px;background:{_pb};border:1.5px solid {_pc};'
                f'border-radius:20px;font-size:0.72rem;font-weight:{_pf};color:{_pc};'
                f'white-space:nowrap">{"✓ " if _pdone else ""}{_pl}</div>'
            )
        _prog_html += f'<div style="margin-left:auto;font-size:0.70rem;color:{EPM_GRAY};align-self:center">{_badge}</div></div>'
        st.markdown(_prog_html, unsafe_allow_html=True)

        # ── Helpers de mapeo PoF/CoF → ordinal (config-driven desde epm_config) ──
        # Los umbrales se leen de risk_matrix para mantener consistencia con epm_core.py.
        # Fallbacks hardcoded solo si la clave no existe (retrocompatibilidad).
        _rm_cfg = epm_config.get("risk_matrix", {})

        def _pof_to_level(pof: float) -> int:
            """Mapea PoF acumulada Weibull F(t) ∈ [0,1] al nivel de probabilidad (1-5).
            Umbrales leídos de risk_matrix.pof_level_thresholds (epm_core.py).
            Recalibración v5.0: P1<5%, P2<20%, P3<40%, P4<65%, P5≥65%.
            """
            _thr = _rm_cfg.get("pof_level_thresholds", [0.05, 0.20, 0.40, 0.65])
            for _lvl, _t in enumerate(_thr, start=1):
                if pof < _t:
                    return _lvl
            return 5

        def _cof_to_level(cof_mm: float) -> int:
            """Mapea CoF en MM COP al nivel de consecuencia (1-5).
            Umbrales leídos de risk_matrix.cof_level_thresholds_mm (epm_core.py).
            Calibración v5.0 (TyD referencia): C1<150M, C2<1000M, C3<7000M, C4<30000M.
            """
            _thr = _rm_cfg.get("cof_level_thresholds_mm", [150, 1_000, 7_000, 30_000])
            for _lvl, _t in enumerate(_thr, start=1):
                if cof_mm < _t:
                    return _lvl
            return 5

        # ── FASE A: Definir Baseline / Do Nothing ─────────────────────────────
        if _w_base_proj is None and st.session_state.get("wizard_results") is None:
            st.markdown("#### ⚠️ Do Nothing — Componentes de Inacción")
            st.caption("Define el riesgo actual del activo y el costo de no actuar. "
                       "Este es el punto de referencia: VNT_Marginal = VNT_Alt − VNT_DoNothing. "
                       "Puede haber más de un componente — por ejemplo, dos activos físicos "
                       "distintos degradándose en el mismo proyecto.")

            st.session_state.setdefault("w_risk_components", [])
            st.session_state.setdefault("_wc_key_gen", 0)

            # Sufijo de "generación" para TODAS las keys de widgets de la captura de
            # riesgo de este componente (Weibull asset_aware + matriz de consecuencias
            # de 4/5 tipos driver_based). Cada "+ Guardar y agregar otro componente"
            # incrementa `_wc_key_gen`, así que el próximo componente usa keys nunca
            # vistas por el frontend. Esto es necesario porque, para st.select_slider,
            # Streamlit NO garantiza que borrar la key de session_state (pop) fuerce
            # al frontend a soltar el valor ya renderizado: el slider puede quedar
            # visualmente en su última posición aunque Python recalcule el nuevo
            # default (comportamiento confirmado empíricamente en este mismo archivo:
            # con la key borrada pero sin cambiar, el slider seguía mostrando L5 aunque
            # `st.select_slider(...)` devolviera 3 del lado de Python). Cambiar la key
            # fuerza un remount limpio del widget, la única forma robusta de resetear.
            _wc_gen = st.session_state["_wc_key_gen"]

            # El valor del text_input de abajo solo puede limpiarse ANTES de que el
            # widget se instancie en este mismo run (Streamlit prohíbe reasignar
            # st.session_state de una key de widget ya instanciada en el run actual).
            # Por eso el botón de guardar no limpia directamente: marca esta bandera
            # y el rerun consecuente la consume aquí, antes de instanciar los widgets.
            if st.session_state.pop("_wc_clear_label_pending", False):
                st.session_state["wi_comp_current_label"] = ""

            if st.session_state["w_risk_components"]:
                with st.expander(
                    f"📋 Componentes ya guardados ({len(st.session_state['w_risk_components'])})",
                    expanded=True,
                ):
                    for _wc_idx, _wc in enumerate(st.session_state["w_risk_components"]):
                        _wc_c1, _wc_c2 = st.columns([5, 1])
                        with _wc_c1:
                            st.markdown(f"**{_wc_idx + 1}. {_wc['label'] or '(sin nombre)'}**")
                        with _wc_c2:
                            if st.button("🗑️ Quitar", key=f"wi_comp_del_{_wc_idx}"):
                                st.session_state["w_risk_components"].pop(_wc_idx)
                                st.rerun()
                    st.caption(
                        "Estos componentes ya están guardados. Completa el formulario de "
                        "abajo para agregar otro, o confirma para usar solo los ya guardados "
                        "más lo que hayas llenado ahora."
                    )

            _wc_prev_label = st.session_state.get("wi_comp_current_label", "")
            _wc_current_label = st.text_input(
                "Nombre de este componente",
                value=_wc_prev_label, key="wi_comp_current_label",
                placeholder="ej. 'Tubería sector norte', 'Costo directo expansión' — "
                            "obligatorio si vas a guardar más de un componente",
            )

            if _w_meth == "asset_aware":
                # ── ASSET-AWARE Do Nothing: condición Weibull del activo ──────
                st.markdown(
                    f'<div style="background:#EFF6FF;border:1px solid #93C5FD;'
                    f'border-radius:8px;padding:8px 14px;margin-bottom:12px;'
                    f'font-size:0.78rem;color:#1E40AF">'
                    f'<b>Asset-Aware:</b> la condición del activo <i>sin intervención</i> '
                    f'define el riesgo base a través de la curva Weibull. '
                    f'Los parámetros aquí describen el activo <b>hoy</b>.</div>',
                    unsafe_allow_html=True)

                if st.session_state.get("w_risk_components"):
                    st.caption(
                        "Cada componente guarda su propia curva Weibull (β/η/edad/CoF). "
                        "El motor suma el ΔR Weibull de todos los componentes y el "
                        "beneficio en pesos (EAL evitada) que alimenta el VNT de las "
                        "alternativas también suma la contribución de cada uno. Los β/η/"
                        "edad de este formulario son sólo los de ESTE componente y no "
                        "afectan a los ya guardados.")

                _af_dn     = ASSET_FAMILIES.get(st.session_state.get("w_asset_family","otro"),
                                                 ASSET_FAMILIES["otro"])
                _wbd_dn    = _af_dn.get("weibull_defaults") or {"beta":3.0,"eta":30.0}
                _prev_aw_dn = st.session_state.get("w_aw_baseline", {})

                _dn1, _dn2, _dn3 = st.columns(3, gap="medium")
                with _dn1:
                    st.markdown("**β — Forma de la curva**")
                    _beta_hint = ("β < 1: fallas tempranas" if _prev_aw_dn.get("beta",3.0) < 1
                                  else "β = 1: fallas aleatorias" if abs(_prev_aw_dn.get("beta",3.0)-1)<0.15
                                  else "β > 1: desgaste por envejecimiento")
                    st.caption(f"Norma: {_wbd_dn.get('norma','')} · {_beta_hint}")
                    _dn_beta = st.number_input(
                        "β", min_value=0.1, max_value=10.0,
                        value=float(_prev_aw_dn.get("beta", _wbd_dn.get("beta", 3.0))),
                        step=0.1, key=f"wi_dn_beta_{_wc_gen}")
                with _dn2:
                    st.markdown("**η — Vida característica (años)**")
                    st.caption("A t = η exactamente el 63.2% de activos han fallado (constante matemática, no varía con β)")
                    # Techo dinámico: transformador_potencia calibra η=290a (CIGRE/Energies
                    # 2026, vida característica matemática, no física — ver ASSET_FAMILIES en
                    # epm_core.py). Un tope fijo en 200 rompía el widget para esa familia.
                    _dn_eta_default = float(_prev_aw_dn.get("eta", _wbd_dn.get("eta", 30.0)))
                    _dn_eta_max     = max(200.0, _dn_eta_default + 50.0)
                    _dn_eta = st.number_input(
                        "η (años)", min_value=1.0, max_value=_dn_eta_max,
                        value=_dn_eta_default,
                        step=1.0, key=f"wi_dn_eta_{_wc_gen}")
                with _dn3:
                    st.markdown("**Edad actual del activo (años)**")
                    st.caption("Desde instalación o último overhaul mayor")
                    _dn_age = st.number_input(
                        "Edad (años)", min_value=0.0, max_value=150.0,
                        value=float(_prev_aw_dn.get("age", 0.0)),
                        step=1.0, key=f"wi_dn_age_{_wc_gen}")

                # ── Métricas Weibull reactivas (cambian con β y η) ────────────
                if _dn_beta > 0 and _dn_eta > 0:
                    _mttf  = _dn_eta * math.gamma(1.0 + 1.0 / _dn_beta)
                    _b10   = _dn_eta * ((-math.log(0.90)) ** (1.0 / _dn_beta))
                    _b50   = _dn_eta * (math.log(2.0)     ** (1.0 / _dn_beta))
                    _life_pct = min(_dn_age / _dn_eta * 100, 999.0)
                    st.markdown(
                        f'<div style="background:#F8FAFC;border:1px solid #E2E8F0;'
                        f'border-radius:8px;padding:8px 14px;margin-top:6px;'
                        f'display:flex;gap:20px;flex-wrap:wrap">'
                        f'<div><div style="font-size:0.62rem;color:{EPM_GRAY};font-weight:600;'
                        f'text-transform:uppercase">MTTF (vida media)</div>'
                        f'<div style="font-size:0.88rem;font-weight:700;color:{EPM_SLATE}">'
                        f'{_mttf:.1f} años</div>'
                        f'<div style="font-size:0.60rem;color:{EPM_GRAY}">η·Γ(1+1/β)</div></div>'
                        f'<div><div style="font-size:0.62rem;color:{EPM_GRAY};font-weight:600;'
                        f'text-transform:uppercase">B10 (10% PoF)</div>'
                        f'<div style="font-size:0.88rem;font-weight:700;color:{EPM_TEAL}">'
                        f'{_b10:.1f} años</div>'
                        f'<div style="font-size:0.60rem;color:{EPM_GRAY}">umbral de intervención</div></div>'
                        f'<div><div style="font-size:0.62rem;color:{EPM_GRAY};font-weight:600;'
                        f'text-transform:uppercase">B50 (mediana)</div>'
                        f'<div style="font-size:0.88rem;font-weight:700;color:{EPM_AMBER}">'
                        f'{_b50:.1f} años</div>'
                        f'<div style="font-size:0.60rem;color:{EPM_GRAY}">50% de la flota ha fallado</div></div>'
                        f'<div style="margin-left:auto">'
                        f'<div style="font-size:0.62rem;color:{EPM_GRAY};font-weight:600;'
                        f'text-transform:uppercase">Edad / η</div>'
                        f'<div style="font-size:0.88rem;font-weight:700;color:'
                        f'{"#DC2626" if _life_pct > 80 else "#D97706" if _life_pct > 50 else EPM_DARK}">'
                        f'{_life_pct:.0f}%</div>'
                        f'<div style="font-size:0.60rem;color:{EPM_GRAY}">de vida útil consumida</div>'
                        f'</div></div>',
                        unsafe_allow_html=True)

                _dn4, _dn5 = st.columns([2, 1], gap="medium")
                with _dn4:
                    st.markdown("**Consecuencia por falla (MM COP)**")
                    st.caption("Costo total de un evento de falla: ENS + correctivo + multas")
                    _sc_dn_price = 0.0
                    _af_key_dn   = st.session_state.get("w_asset_family", "otro")
                    for _sc_dn in BU_DRIVER_CONFIG.get(_w_bu, {}).get("sub_criteria", {}).values():
                        if (_sc_dn.get("asset_family") == _af_key_dn
                                and _sc_dn.get("metodologia_sugerida") == "asset_aware"):
                            _sc_dn_price = float(_sc_dn.get("price_COP", 0) or 0) / 1e6
                            break
                    _dn_cof = st.number_input(
                        "CoF (MM COP/evento)", min_value=0.0, max_value=100_000.0,
                        value=float(_prev_aw_dn.get("cof_mm", max(_sc_dn_price, 1.0))),
                        step=10.0, key=f"wi_dn_cof_{_wc_gen}",
                        help="Precio regulatorio del sub-criterio como referencia inicial")
                    _dn_opex = st.number_input(
                        "OPEX correctivo adicional (MM COP/año)",
                        min_value=0.0, max_value=50_000.0,
                        value=float(_prev_aw_dn.get("opex_mm", 0.0)),
                        step=5.0, key=f"wi_dn_opex_{_wc_gen}",
                        help="Mantenimiento correctivo incremental sin intervención")

                    # ── Componente A: Recurrente (siempre activo) ─────────────
                    st.caption(
                        "🔁 **Componente A** — Recurrente · ENS, correctivo, "
                        "pérdida producción anual")
                    # ── Componente B: Evento único (opcional) ─────────────────
                    _dn_b_enabled = st.checkbox(
                        "＋ Componente B — Evento único (multa, compensación, catástrofe)",
                        value=False,
                        key=f"wi_dn_cof_b_enable_{_wc_gen}",
                        help=(
                            "Habilita un segundo componente financiero de carácter único: "
                            "ocurre UNA vez cuando el activo falla (multa regulatoria, "
                            "compensación a comunidad, pérdida catastrófica).\n\n"
                            "El modelo calcula el VPE con la PDF Weibull f(t) sobre el "
                            "horizonte y lo anualiza vía CRF. "
                            "EAL total = EAL-A (recurrente) + EAL-B (evento único)."
                        ),
                    )
                    if _dn_b_enabled:
                        _dn_cof_b = st.number_input(
                            "CoF-B — Evento único (MM COP/evento)",
                            min_value=0.0, max_value=100_000.0,
                            value=max(_sc_dn_price * 2.0, 1.0),
                            step=10.0, key=f"wi_dn_cof_b_inp_{_wc_gen}",
                            help="Impacto financiero de UNA ocurrencia de falla "
                                 "(multa, compensación, catástrofe). "
                                 "Independiente del CoF recurrente Componente A.")
                    else:
                        _dn_cof_b = 0.0

                with _dn5:
                    # ── Cómputo dual-componente (A recurrente + B evento único) ─
                    _dn_wacc = epm_config.get("wacc", 0.10)
                    _dn_hz   = int(epm_config.get("analysis_horizon_years", 10))

                    if _dn_eta > 0 and _dn_beta > 0:
                        _dn_pof = 1.0 - math.exp(-((_dn_age / _dn_eta) ** _dn_beta))

                        # ── Componente A: Recurrente ───────────────────────────
                        _dn_eal_a = _dn_pof * _dn_cof   # EAL-A = PoF × CoF-A

                        # ── Componente B: Evento único (VPE Weibull) ──────────
                        if _dn_b_enabled and _dn_cof_b > 0:
                            # VPE-B = Σ f(t) × CoF-B × (1+wacc)^(-t) t=1..hz
                            # f(t) = (β/η)·(t/η)^(β-1)·exp(-(t/η)^β)
                            _dn_vpe = 0.0
                            for _yr in range(1, _dn_hz + 1):
                                _t_yr = float(_dn_age + _yr)
                                try:
                                    _f_t = (((_dn_beta / _dn_eta)
                                             * (_t_yr / _dn_eta) ** (_dn_beta - 1)
                                             * math.exp(-(_t_yr / _dn_eta) ** _dn_beta))
                                            if _t_yr > 0 else 0.0)
                                except (OverflowError, ZeroDivisionError):
                                    _f_t = 0.0
                                _dn_vpe += _f_t * _dn_cof_b / ((1 + _dn_wacc) ** _yr)
                            # Anualizar VPE-B vía CRF
                            _crf_dn = (_dn_wacc * (1 + _dn_wacc) ** _dn_hz /
                                       ((1 + _dn_wacc) ** _dn_hz - 1)) if _dn_hz > 0 else 1.0
                            _dn_vpe_annual = _dn_vpe * _crf_dn
                            _dn_eal_b = _dn_vpe_annual
                        else:
                            _dn_vpe = None
                            _dn_vpe_annual = None
                            _dn_eal_b = 0.0

                        # EAL total financiero = A + B
                        _dn_eal = _dn_eal_a + _dn_eal_b
                    else:
                        _dn_pof = 0.0
                        _dn_eal_a = 0.0
                        _dn_eal_b = 0.0
                        _dn_eal   = 0.0
                        _dn_vpe   = None
                        _dn_vpe_annual = None

                    _dn_color = (EPM_RED if _dn_pof > 0.5 else
                                 EPM_AMBER if _dn_pof > 0.2 else EPM_DARK)

                    st.markdown("**PoF actual (sin intervención)**")
                    st.caption("Prob. falla acumulada a la edad actual")

                    if _dn_b_enabled and _dn_vpe is not None and _dn_vpe > 0:
                        # Tarjeta dual: PoF + EAL-A + VPE-B + EAL total
                        st.markdown(
                            f'<div style="background:{_dn_color}15;border:2px solid {_dn_color};'
                            f'border-radius:10px;padding:10px;text-align:center;margin-top:4px">'
                            f'<div style="font-size:1.5rem;font-weight:800;color:{_dn_color}">'
                            f'{_dn_pof:.1%}</div>'
                            f'<div style="font-size:0.66rem;color:{EPM_GRAY};margin-top:3px">'
                            f'EAL-A (rec.): {_dn_eal_a:,.1f} MM/año</div>'
                            f'<div style="font-size:0.64rem;color:#a855f7;font-weight:600">'
                            f'VPE-B: {_dn_vpe:,.1f} MM → {_dn_eal_b:,.1f} MM/año</div>'
                            f'<div style="font-size:0.72rem;font-weight:800;'
                            f'color:{_dn_color};border-top:1px solid {_dn_color}33;'
                            f'margin-top:5px;padding-top:4px">'
                            f'EAL total: {_dn_eal:,.1f} MM/año</div>'
                            f'<div style="font-size:0.58rem;color:{EPM_GRAY}">'
                            f'{_dn_hz}a · WACC {_dn_wacc:.0%}</div></div>',
                            unsafe_allow_html=True)
                    else:
                        st.markdown(
                            f'<div style="background:{_dn_color}15;border:2px solid {_dn_color};'
                            f'border-radius:10px;padding:12px;text-align:center;margin-top:4px">'
                            f'<div style="font-size:1.5rem;font-weight:800;color:{_dn_color}">'
                            f'{_dn_pof:.1%}</div>'
                            f'<div style="font-size:0.68rem;color:{EPM_GRAY};margin-top:2px">'
                            f'EAL-A: {_dn_eal:,.1f} MM/año</div>'
                            f'<div style="font-size:0.64rem;color:{EPM_GRAY}">'
                            f'Recurrente · solo Comp. A activo</div></div>',
                            unsafe_allow_html=True)

                # Derivar niveles ordinales para el motor (compatibilidad RiskInput)
                _b_rbc = _cof_to_level(_dn_cof)
                _b_rbp = _pof_to_level(_dn_pof)
                _b_opex = _dn_opex

                st.caption(
                    f"Equivalencia matriz de riesgo — Financiero: C{_b_rbc} × P{_b_rbp} = "
                    f"{_b_rbc*_b_rbp} pts — derivado de Weibull automáticamente.")

                # ── Tipos de consecuencia adicionales (H&S, Ambiental, etc.) ──
                # El riesgo FINANCIERO proviene del modelo Weibull (arriba).
                # Los demás tipos (H&S, Ambiental, Regulatorio, Reputacional)
                # se ingresan manualmente — son dimensiones independientes al
                # riesgo físico del activo y NO están capturadas por Weibull.
                _ctm_cfg_aw = epm_config.get("risk_matrix", {}).get("consequence_types_matrix", {})
                _supp_types_aw  = ["hs", "ambiental", "regulatorio", "reputacional"]
                _cof_lvl_lbl_aw = {1:"L1 — Insignificante", 2:"L2 — Menor",
                                   3:"L3 — Moderada", 4:"L4 — Alta", 5:"L5 — Catastrófica"}
                _pof_lbl_aw     = {1:"P1-Rara", 2:"P2-Baja", 3:"P3-Posible",
                                   4:"P4-Probable", 5:"P5-Seguro"}

                # Inicializar con el tipo financiero derivado de Weibull
                _b_consequence_types:       dict = {"financiero": _b_rbc}
                _b_consequence_type_probs:  dict = {"financiero": _b_rbp}

                with st.expander(
                    "➕ Consecuencias adicionales — H&S · Ambiental · Regulatorio · Reputacional",
                    expanded=False,
                ):
                    st.caption(
                        "El riesgo **Financiero** ya está calculado por Weibull. "
                        "Ingresa aquí los demás tipos de impacto que apliquen. "
                        "Cada tipo soporta **Componente A** (recurrente/anual) y "
                        "**Componente B** (evento único, opcional). "
                        "Niveles L1 en ambos componentes excluyen el tipo de la matriz.")
                    for _cof_t_aw in _supp_types_aw:
                        _cof_tdata_aw = _ctm_cfg_aw.get(_cof_t_aw, {})
                        if not _cof_tdata_aw:
                            continue
                        _cof_label_aw = _cof_tdata_aw.get("label", _cof_t_aw.capitalize())
                        _cof_icon_aw  = _cof_tdata_aw.get("icon", "")
                        _cof_bu_lvls_aw = _cof_tdata_aw.get("levels_by_bu", {}).get(_w_bu, {})
                        if not _cof_bu_lvls_aw:
                            _cof_bu_lvls_aw = next(
                                iter(_cof_tdata_aw.get("levels_by_bu", {}).values()), {})

                        st.markdown(
                            f'<div style="font-size:0.72rem;font-weight:700;color:#94A3B8;'
                            f'margin-top:8px;margin-bottom:2px">'
                            f'{_cof_icon_aw} {_cof_label_aw}</div>',
                            unsafe_allow_html=True)

                        # ── Componente A: Recurrente ───────────────────────────
                        _cca1, _cca2, _cca3 = st.columns([1, 1, 2], gap="small")
                        with _cca1:
                            _prev_cof_aw_a = st.session_state.get(
                                f"wi_dn_ct_{_cof_t_aw}_a_{_wc_gen}", 1)
                            _sel_cof_aw_a = st.select_slider(
                                f"Comp-A {_cof_label_aw} (rec.)",
                                options=[1, 2, 3, 4, 5],
                                value=_prev_cof_aw_a,
                                format_func=lambda x: _cof_lvl_lbl_aw[x],
                                key=f"wi_dn_cof_{_cof_t_aw}_a_inp_{_wc_gen}",
                                help="Componente A — consecuencia recurrente / anual",
                            )
                            st.session_state[f"wi_dn_ct_{_cof_t_aw}_a_{_wc_gen}"] = _sel_cof_aw_a
                        with _cca2:
                            _prev_pof_aw_a = st.session_state.get(
                                f"wi_dn_pt_{_cof_t_aw}_a_{_wc_gen}", 1)
                            _sel_pof_aw_a = st.select_slider(
                                f"PoF-A {_cof_label_aw}",
                                options=[1, 2, 3, 4, 5],
                                value=_prev_pof_aw_a,
                                format_func=lambda x: _pof_lbl_aw[x],
                                key=f"wi_dn_pof_{_cof_t_aw}_a_inp_{_wc_gen}",
                            )
                            st.session_state[f"wi_dn_pt_{_cof_t_aw}_a_{_wc_gen}"] = _sel_pof_aw_a
                        with _cca3:
                            _anch_aw_a = (_cof_bu_lvls_aw.get(str(_sel_cof_aw_a), {})
                                          .get("anchor", "—"))
                            _val_aw_a  = (_cof_bu_lvls_aw.get(str(_sel_cof_aw_a), {})
                                          .get("value_COP", 0)) / 1e6
                            st.markdown(
                                f'<div style="background:#F0F9FF;border:1px solid #BAE6FD;'
                                f'border-radius:6px;padding:5px 10px;margin-top:20px;'
                                f'font-size:0.72rem;color:#0369A1">'
                                f'A-Rec · {_val_aw_a:,.0f} MM COP<br>'
                                f'<span style="color:#94A3B8;font-size:0.67rem">'
                                f'{_anch_aw_a}</span></div>',
                                unsafe_allow_html=True)

                        # ── Componente B: Evento único (opcional) ─────────────
                        _aw_b_enabled = st.checkbox(
                            f"＋ Comp-B evento único — {_cof_label_aw}",
                            value=False,
                            key=f"wi_dn_{_cof_t_aw}_b_enable_{_wc_gen}",
                            help=f"Habilita un componente de evento único para {_cof_label_aw} "
                                 f"(ej: multa puntual, fallecimiento, sanción). "
                                 f"Nivel para la matriz = max(A, B); PoF = max(pof-A, pof-B).",
                        )
                        if _aw_b_enabled:
                            _ccb1, _ccb2, _ccb3 = st.columns([1, 1, 2], gap="small")
                            with _ccb1:
                                _prev_cof_aw_b = st.session_state.get(
                                    f"wi_dn_ct_{_cof_t_aw}_b_{_wc_gen}", 1)
                                _sel_cof_aw_b = st.select_slider(
                                    f"Comp-B {_cof_label_aw} (único)",
                                    options=[1, 2, 3, 4, 5],
                                    value=_prev_cof_aw_b,
                                    format_func=lambda x: _cof_lvl_lbl_aw[x],
                                    key=f"wi_dn_cof_{_cof_t_aw}_b_inp_{_wc_gen}",
                                    help="Nivel de consecuencia del evento único",
                                )
                                st.session_state[f"wi_dn_ct_{_cof_t_aw}_b_{_wc_gen}"] = _sel_cof_aw_b
                            with _ccb2:
                                _prev_pof_aw_b = st.session_state.get(
                                    f"wi_dn_pt_{_cof_t_aw}_b_{_wc_gen}", 1)
                                _sel_pof_aw_b = st.select_slider(
                                    f"PoF-B {_cof_label_aw}",
                                    options=[1, 2, 3, 4, 5],
                                    value=_prev_pof_aw_b,
                                    format_func=lambda x: _pof_lbl_aw[x],
                                    key=f"wi_dn_pof_{_cof_t_aw}_b_inp_{_wc_gen}",
                                )
                                st.session_state[f"wi_dn_pt_{_cof_t_aw}_b_{_wc_gen}"] = _sel_pof_aw_b
                            with _ccb3:
                                _anch_aw_b = (_cof_bu_lvls_aw.get(str(_sel_cof_aw_b), {})
                                              .get("anchor", "—"))
                                _val_aw_b  = (_cof_bu_lvls_aw.get(str(_sel_cof_aw_b), {})
                                              .get("value_COP", 0)) / 1e6
                                st.markdown(
                                    f'<div style="background:#FDF4FF;border:1px solid #E9D5FF;'
                                    f'border-radius:6px;padding:5px 10px;margin-top:20px;'
                                    f'font-size:0.72rem;color:#7C3AED">'
                                    f'B-Único · {_val_aw_b:,.0f} MM COP<br>'
                                    f'<span style="color:#94A3B8;font-size:0.67rem">'
                                    f'{_anch_aw_b}</span></div>',
                                    unsafe_allow_html=True)
                        else:
                            _sel_cof_aw_b = 1
                            _sel_pof_aw_b = 1

                        # Nivel consolidado para la matriz = max(A, B)
                        _sel_cof_aw = max(_sel_cof_aw_a, _sel_cof_aw_b)
                        _sel_pof_aw = max(_sel_pof_aw_a, _sel_pof_aw_b)

                        # Solo incluir si al menos un componente supera L1
                        if _sel_cof_aw > 1:
                            _b_consequence_types[_cof_t_aw]      = _sel_cof_aw
                            _b_consequence_type_probs[_cof_t_aw] = _sel_pof_aw

                # ── Matriz de Riesgo 5×5 — Do Nothing (asset_aware) ──────────
                st.markdown("##### 🟥 Posición de Riesgo — Do Nothing")
                st.caption(
                    "Riesgo Financiero derivado del modelo Weibull. "
                    "Tipos adicionales ingresados en el panel de arriba. "
                    "Las alternativas mostrarán su desplazamiento residual.")
                _all_scores_dn = [_b_consequence_types[t] * _b_consequence_type_probs[t]
                                  for t in _b_consequence_types]
                _max_score_dn  = max(_all_scores_dn) if _all_scores_dn else _b_rbc * _b_rbp
                _fig_rm_dn = _build_risk_matrix_fig(
                    bl_types=_b_consequence_types,
                    bl_probs=_b_consequence_type_probs,
                    ctm_config=_ctm_cfg_aw,
                    title=f"Do Nothing — score máx. {_max_score_dn} pts "
                          f"({len(_b_consequence_types)} tipo{'s' if len(_b_consequence_types)>1 else ''})",
                )
                st.plotly_chart(_fig_rm_dn, use_container_width=True,
                                config={"displayModeBar": False})

                # ── Gráfica Weibull interactiva — 4 vistas ────────────────────
                st.markdown("---")
                st.markdown("##### 📈 Curva de falla Weibull — actualiza en tiempo real")

                # Selector de vista
                _wb_view = st.radio(
                    "Vista",
                    ["PoF + EAL", "Confiabilidad R(t)",
                     "Tasa de Riesgo h(t)", "Comparativa β"],
                    horizontal=True,
                    key="wi_dn_wb_view",
                    label_visibility="collapsed",
                )

                # ── Dominio temporal ──────────────────────────────────────────
                _t_max = max(2.0 * _dn_eta, _dn_age * 1.5, 80.0)
                _t_pts = 500
                _t_arr = [_t_max * i / (_t_pts - 1) for i in range(_t_pts)]

                # ── Funciones auxiliares Weibull ──────────────────────────────
                def _weibull_pof(t_arr, beta, eta):
                    """F(t) = PoF(t) = 1 − exp(−(t/η)^β)"""
                    return [1.0 - math.exp(-((t / eta) ** beta)) if t > 0 else 0.0
                            for t in t_arr]

                def _weibull_pdf(t_arr, beta, eta):
                    """f(t) = (β/η)·(t/η)^(β−1)·exp(−(t/η)^β) — densidad de falla"""
                    out = []
                    for t in t_arr:
                        if t <= 0:
                            out.append(0.0)
                        else:
                            try:
                                v = ((beta / eta) * ((t / eta) ** (beta - 1))
                                     * math.exp(-((t / eta) ** beta)))
                                out.append(v if math.isfinite(v) else 0.0)
                            except (OverflowError, ZeroDivisionError):
                                out.append(0.0)
                    return out

                def _weibull_hazard(t_arr, beta, eta):
                    """h(t) = (β/η)·(t/η)^(β−1) — tasa instantánea de falla"""
                    out = []
                    for t in t_arr:
                        if t <= 0:
                            out.append(0.0)
                        else:
                            try:
                                v = (beta / eta) * ((t / eta) ** (beta - 1))
                                out.append(v if math.isfinite(v) else 0.0)
                            except (OverflowError, ZeroDivisionError):
                                out.append(0.0)
                    return out

                # ── Curvas principales ────────────────────────────────────────
                _pof_main  = _weibull_pof(_t_arr, _dn_beta, _dn_eta)
                _rel_main  = [1.0 - p for p in _pof_main]
                _eal_arr   = [p * _dn_cof for p in _pof_main]
                _pdf_main  = _weibull_pdf(_t_arr, _dn_beta, _dn_eta)
                _hz_raw    = _weibull_hazard(_t_arr, _dn_beta, _dn_eta)
                # G4: usar percentil 99 como cap para que β alto no aplaste la escala
                _hz_finite = sorted(_h for _h in _hz_raw if math.isfinite(_h) and _h > 0)
                _hz_p99    = (_hz_finite[int(len(_hz_finite) * 0.99)]
                              if len(_hz_finite) > 10 else
                              max(_hz_finite, default=1.0))
                _hz_arr    = [min(_h, _hz_p99) if math.isfinite(_h) else 0.0
                              for _h in _hz_raw]

                # ── Banda de sensibilidad β ± 0.5 ─────────────────────────────
                _beta_hi  = min(_dn_beta + 0.5, 10.0)
                _beta_lo  = max(_dn_beta - 0.5, 0.3)
                _pof_hi   = _weibull_pof(_t_arr, _beta_hi, _dn_eta)
                _pof_lo   = _weibull_pof(_t_arr, _beta_lo, _dn_eta)

                # ── Hitos de ciclo de vida ────────────────────────────────────
                _b10_pof  = 1.0 - math.exp(-((_b10 / _dn_eta) ** _dn_beta)) if _dn_eta > 0 else 0.0
                _b50_pof  = 1.0 - math.exp(-((_b50 / _dn_eta) ** _dn_beta)) if _dn_eta > 0 else 0.0
                _eta_pof  = 1.0 - math.exp(-1.0)   # 63.2 % siempre
                _age_eal  = _dn_pof * _dn_cof

                # Mode = η·((β−1)/β)^(1/β)  [sólo existe para β > 1]
                _wb_mode = (
                    _dn_eta * ((_dn_beta - 1) / _dn_beta) ** (1.0 / _dn_beta)
                    if _dn_beta > 1.0 else None
                )
                _wb_mode_pof = (
                    1.0 - math.exp(-((_wb_mode / _dn_eta) ** _dn_beta))
                    if _wb_mode is not None and _dn_eta > 0 else None
                )

                # ── Métricas para el recuadro de conclusión ───────────────────
                # f(t) evaluada en la edad actual → ΔEAL/año
                _wb_pdf_now = (
                    ((_dn_beta / _dn_eta) * ((_dn_age / _dn_eta) ** (_dn_beta - 1))
                     * math.exp(-((_dn_age / _dn_eta) ** _dn_beta)))
                    if _dn_age > 0 and _dn_eta > 0 else 0.0
                )
                _wb_deal_yr    = _wb_pdf_now * _dn_cof          # MM COP de EAL por año adicional
                # G2: distinguir régimen real en lugar de booleano aceleración
                # β<1 → hazard decreciente (mortalidad infantil), β≈1 → constante, β>1 → creciente
                if _dn_beta < 0.9:
                    _wb_regime_phase = "decelerating"   # mort. infantil
                elif _dn_beta < 1.1:
                    _wb_regime_phase = "constant"       # exponencial
                elif _wb_mode is None or _dn_age < _wb_mode:
                    _wb_regime_phase = "accelerating"   # antes del pico
                else:
                    _wb_regime_phase = "decelerating_wear"  # tras el pico (desgaste)
                _wb_to_b50     = max(0.0, _b50 - _dn_age)
                _wb_to_mode    = (max(0.0, _wb_mode - _dn_age) if _wb_mode else None)
                _wb_to_eta     = max(0.0, _dn_eta - _dn_age)
                # EAL proyectado a 5 años
                _wb_pof_5yr    = (1.0 - math.exp(-(( (_dn_age + 5.0) / _dn_eta) ** _dn_beta))
                                  if _dn_eta > 0 else _dn_pof)
                _wb_eal_5yr    = _wb_pof_5yr * _dn_cof

                # G1: zona de riesgo config-driven — 4 niveles alineados con pof_level_thresholds
                # [t0, t1, t2, t3] → P1-P2 BAJO | P3 MODERADO | P4 ALTO | P5 CRÍTICO
                _pof_thr_z = (_rm_cfg.get("pof_level_thresholds")
                              or [0.05, 0.20, 0.40, 0.65])
                # umbral P2/P3, P3/P4, P4/P5 (índices 1, 2, 3)
                _z_t_low  = _pof_thr_z[1] if len(_pof_thr_z) > 1 else 0.20  # 0.20
                _z_t_mod  = _pof_thr_z[2] if len(_pof_thr_z) > 2 else 0.40  # 0.40
                _z_t_high = _pof_thr_z[3] if len(_pof_thr_z) > 3 else 0.65  # 0.65
                if _dn_pof < _z_t_low:
                    _wb_risk_zone  = "BAJO"
                    _wb_risk_color = "#22c55e"    # verde
                    _wb_risk_bg    = "#052e16"
                elif _dn_pof < _z_t_mod:
                    _wb_risk_zone  = "MODERADO"
                    _wb_risk_color = "#f59e0b"    # ámbar
                    _wb_risk_bg    = "#451a03"
                elif _dn_pof < _z_t_high:
                    _wb_risk_zone  = "ALTO"
                    _wb_risk_color = "#ef4444"    # rojo
                    _wb_risk_bg    = "#450a0a"
                else:
                    _wb_risk_zone  = "CRÍTICO"
                    _wb_risk_color = "#dc2626"    # rojo oscuro
                    _wb_risk_bg    = "#3b0000"

                # ── Construir figura según vista seleccionada ─────────────────
                _fig_wb = make_subplots(specs=[[{"secondary_y": True}]])

                # Helper: añadir zonas de riesgo (sólo en vistas PoF y Confiabilidad)
                # G1: 4 bandas alineadas con pof_level_thresholds del config
                def _add_risk_zones(fig, invert=False):
                    """Rectángulos semitransparentes sobre eje Y [0,1] — 4 niveles config."""
                    _zones = [
                        (0.00,       _z_t_low,  "rgba(34,197,94,0.07)"),   # verde  BAJO
                        (_z_t_low,   _z_t_mod,  "rgba(245,158,11,0.07)"),  # ámbar  MODERADO
                        (_z_t_mod,   _z_t_high, "rgba(239,68,68,0.08)"),   # rojo   ALTO
                        (_z_t_high,  1.02,      "rgba(185,28,28,0.10)"),   # rojo oscuro CRÍTICO
                    ]
                    for _y0, _y1, _fc in _zones:
                        _lo, _hi = (1 - _y1, 1 - _y0) if invert else (_y0, _y1)
                        fig.add_hrect(y0=_lo, y1=_hi, fillcolor=_fc,
                                      line_width=0, layer="below")

                if _wb_view == "PoF + EAL":
                    _add_risk_zones(_fig_wb, invert=False)

                    # f(t) PDF como área sombreada (escala independiente — eje secundario)
                    # max(..., 1e-9): guarda contra ZeroDivisionError cuando todos los valores son 0
                    _pdf_max = max(max(_pdf_main), 1e-9) if _pdf_main else 1e-9
                    _pdf_norm = [v / _pdf_max for v in _pdf_main]   # normalizado 0–1 para solapar
                    _fig_wb.add_trace(go.Scatter(
                        x=_t_arr, y=_pdf_norm, mode="lines",
                        line=dict(width=0), fill="tozeroy",
                        fillcolor="rgba(99,102,241,0.18)",
                        name="f(t) PDF (forma)",
                        hovertemplate="t=%{x:.1f}a  f(t)=%{customdata:.5f}/a<extra>PDF densidad</extra>",
                        customdata=_pdf_main,
                        showlegend=True,
                    ), secondary_y=False)

                    # Banda sensibilidad β (sólo fill, sin líneas en leyenda)
                    _fig_wb.add_trace(go.Scatter(
                        x=_t_arr, y=_pof_hi, mode="lines",
                        line=dict(width=0), showlegend=False, hoverinfo="skip",
                    ), secondary_y=False)
                    _fig_wb.add_trace(go.Scatter(
                        x=_t_arr, y=_pof_lo, mode="lines",
                        line=dict(width=0), fill="tonexty",
                        fillcolor="rgba(30,64,175,0.12)",
                        name=f"Banda β ±0.5", hoverinfo="skip",
                        showlegend=True,
                    ), secondary_y=False)

                    # Curva PoF principal
                    _fig_wb.add_trace(go.Scatter(
                        x=_t_arr, y=_pof_main, mode="lines",
                        line=dict(color=EPM_BLUE, width=3.0),
                        name=f"PoF  β={_dn_beta:.1f}  η={_dn_eta:.0f}a",
                        hovertemplate="t=%{x:.1f}a  PoF=%{y:.1%}<extra>Weibull</extra>",
                    ), secondary_y=False)

                    # EAL eje secundario (línea delgada)
                    _fig_wb.add_trace(go.Scatter(
                        x=_t_arr, y=_eal_arr, mode="lines",
                        line=dict(color=EPM_AMBER, width=1.8, dash="dot"),
                        name=f"EAL (×CoF {_dn_cof:,.0f})",
                        hovertemplate="t=%{x:.1f}a  EAL=%{y:,.1f} MM<extra>EAL</extra>",
                    ), secondary_y=True)

                    # Hitos: B10, B50, η + Mode (si β>1)
                    _hx = [_b10, _b50, _dn_eta]
                    _hy = [_b10_pof, _b50_pof, _eta_pof]
                    _ht = ["B10", "B50", "η(63%)"]
                    _hc = [EPM_TEAL, EPM_AMBER, EPM_DARK]
                    if _wb_mode is not None and _wb_mode_pof is not None:
                        _hx.append(_wb_mode)
                        _hy.append(_wb_mode_pof)
                        _ht.append("Mode")
                        _hc.append("#a855f7")   # violeta para Mode
                    _fig_wb.add_trace(go.Scatter(
                        x=_hx, y=_hy, mode="markers+text",
                        marker=dict(size=10, color=_hc, symbol="circle",
                                    line=dict(width=1.5, color="white")),
                        text=_ht,
                        textposition=["top right", "top left", "top right"]
                                      + (["top center"] if _wb_mode else []),
                        textfont=dict(size=9),
                        name="Hitos ciclo de vida",
                        hovertemplate="%{text}: t=%{x:.1f}a  PoF=%{y:.1%}<extra></extra>",
                        showlegend=True,
                    ), secondary_y=False)

                    # Punto actual
                    _fig_wb.add_trace(go.Scatter(
                        x=[_dn_age], y=[_dn_pof], mode="markers",
                        marker=dict(size=14, color=EPM_RED, symbol="diamond",
                                    line=dict(width=2, color="white")),
                        name=f"Hoy {_dn_pof:.1%}",
                        hovertemplate=(f"Edad: {_dn_age:.0f}a<br>"
                                       f"PoF = {_dn_pof:.1%}<br>"
                                       f"EAL = {_age_eal:,.1f} MM<extra>Hoy</extra>"),
                        showlegend=True,
                    ), secondary_y=False)

                    _fig_wb.add_vline(x=_dn_age,
                        line=dict(color=EPM_RED, width=1.8, dash="dash"),
                        annotation_text=f"  Hoy: {_dn_age:.0f}a → {_dn_pof:.1%}",
                        annotation_font=dict(size=10, color=EPM_RED),
                        annotation_position="top right")
                    _fig_wb.add_hline(y=0.50,
                        line=dict(color="rgba(239,68,68,0.45)", width=1.0, dash="dot"))

                    _wb_y1 = dict(title="PoF  /  PDF norm.",
                                  title_font=dict(size=11, color=EPM_BLUE),
                                  tickformat=".0%", range=[0, 1.05],
                                  tickfont=dict(size=10, color=EPM_BLUE),
                                  gridcolor="rgba(255,255,255,0.05)")
                    _wb_y2 = dict(title="EAL (MM COP/año)",
                                  title_font=dict(size=11, color=EPM_AMBER),
                                  tickfont=dict(size=10, color=EPM_AMBER),
                                  showgrid=False)

                elif _wb_view == "Confiabilidad R(t)":
                    _add_risk_zones(_fig_wb, invert=True)

                    _fig_wb.add_trace(go.Scatter(
                        x=_t_arr, y=_rel_main, mode="lines",
                        line=dict(color=EPM_GREEN, width=3.0),
                        fill="tozeroy", fillcolor="rgba(34,197,94,0.08)",
                        name="R(t) = 1 − PoF",
                        hovertemplate="t=%{x:.1f}a  R(t)=%{y:.1%}<extra>Confiabilidad</extra>",
                    ), secondary_y=False)

                    # Hitos (espejo 1-PoF)
                    _hx_r = [_b10, _b50, _dn_eta]
                    _hy_r = [1 - _b10_pof, 1 - _b50_pof, 1 - _eta_pof]
                    _ht_r = ["B10", "B50", "η"]
                    _hc_r = [EPM_TEAL, EPM_AMBER, EPM_DARK]
                    if _wb_mode is not None and _wb_mode_pof is not None:
                        _hx_r.append(_wb_mode)
                        _hy_r.append(1 - _wb_mode_pof)
                        _ht_r.append("Mode")
                        _hc_r.append("#a855f7")
                    _fig_wb.add_trace(go.Scatter(
                        x=_hx_r, y=_hy_r, mode="markers+text",
                        marker=dict(size=10, color=_hc_r, symbol="circle",
                                    line=dict(width=1.5, color="white")),
                        text=_ht_r,
                        textposition=["bottom right", "bottom left", "bottom right"]
                                      + (["top center"] if _wb_mode else []),
                        textfont=dict(size=9),
                        name="Hitos",
                        hovertemplate="%{text}: t=%{x:.1f}a  R=%{y:.1%}<extra></extra>",
                    ), secondary_y=False)
                    _fig_wb.add_trace(go.Scatter(
                        x=[_dn_age], y=[1 - _dn_pof], mode="markers",
                        marker=dict(size=14, color=EPM_RED, symbol="diamond",
                                    line=dict(width=2, color="white")),
                        name=f"Hoy R={1-_dn_pof:.1%}",
                        hovertemplate=(f"Edad: {_dn_age:.0f}a<br>"
                                       f"R(t) = {1-_dn_pof:.1%}<extra>Hoy</extra>"),
                    ), secondary_y=False)
                    _fig_wb.add_vline(x=_dn_age,
                        line=dict(color=EPM_RED, width=1.8, dash="dash"),
                        annotation_text=f"  Hoy: {_dn_age:.0f}a → R={1-_dn_pof:.1%}",
                        annotation_font=dict(size=10, color=EPM_RED),
                        annotation_position="top right")
                    _fig_wb.add_hline(y=0.50,
                        line=dict(color="rgba(239,68,68,0.45)", width=1.0, dash="dot"))
                    _wb_y1 = dict(title="Confiabilidad R(t)",
                                  title_font=dict(size=11, color=EPM_GREEN),
                                  tickformat=".0%", range=[0, 1.05],
                                  tickfont=dict(size=10, color=EPM_GREEN),
                                  gridcolor="rgba(255,255,255,0.05)")
                    _wb_y2 = dict(showgrid=False)

                elif _wb_view == "Tasa de Riesgo h(t)":
                    # f(t) como fondo (área) para dar contexto a h(t)
                    _pdf_max2 = max(max(_pdf_main), 1e-9) if _pdf_main else 1e-9
                    _hz_scale = max(max(_hz_arr), 1e-9) if _hz_arr else 1e-9
                    _pdf_scaled = [v / _pdf_max2 * _hz_scale for v in _pdf_main]
                    _fig_wb.add_trace(go.Scatter(
                        x=_t_arr, y=_pdf_scaled, mode="lines",
                        line=dict(width=0), fill="tozeroy",
                        fillcolor="rgba(99,102,241,0.12)",
                        name="f(t) proporcional", hoverinfo="skip",
                        showlegend=True,
                    ), secondary_y=False)
                    _fig_wb.add_trace(go.Scatter(
                        x=_t_arr, y=_hz_arr, mode="lines",
                        line=dict(color=EPM_RED, width=3.0),
                        name="h(t)  hazard rate",
                        hovertemplate="t=%{x:.1f}a  h(t)=%{y:.6f}/a<extra>h(t)</extra>",
                    ), secondary_y=False)
                    _hz_now = (((_dn_beta / _dn_eta) * ((_dn_age / _dn_eta) ** (_dn_beta - 1)))
                               if _dn_age > 0 else 0.0)
                    _fig_wb.add_trace(go.Scatter(
                        x=[_dn_age], y=[min(_hz_now, _hz_cap * 3)], mode="markers",
                        marker=dict(size=14, color=EPM_RED, symbol="diamond",
                                    line=dict(width=2, color="white")),
                        name=f"Hoy h={_hz_now:.5f}/a",
                        hovertemplate=(f"Edad: {_dn_age:.0f}a<br>"
                                       f"h(t) = {_hz_now:.6f}/año<extra>Hoy</extra>"),
                    ), secondary_y=False)
                    if _wb_mode is not None:
                        _hz_mode = ((_dn_beta / _dn_eta) * ((_wb_mode / _dn_eta) ** (_dn_beta - 1))
                                    if _wb_mode > 0 else 0.0)
                        _fig_wb.add_vline(x=_wb_mode,
                            line=dict(color="#a855f7", width=1.5, dash="dot"),
                            annotation_text=f"  Mode: {_wb_mode:.1f}a",
                            annotation_font=dict(size=9, color="#a855f7"),
                            annotation_position="top left")
                    _fig_wb.add_vline(x=_dn_age,
                        line=dict(color=EPM_RED, width=1.8, dash="dash"),
                        annotation_text=f"  Hoy: {_dn_age:.0f}a",
                        annotation_font=dict(size=10, color=EPM_RED),
                        annotation_position="top right")
                    _wb_regime = (
                        "β < 1 — tasa decreciente (mortalidad infantil)"
                        if _dn_beta < 0.9 else
                        "β ≈ 1 — tasa constante (fallas aleatorias / exponencial)"
                        if _dn_beta < 1.1 else
                        f"β = {_dn_beta:.1f} — tasa creciente (desgaste por envejecimiento)"
                    )
                    st.caption(f"⚙️ Régimen: {_wb_regime}")
                    _wb_y1 = dict(title="Tasa de Riesgo h(t)  [fallas/año]",
                                  title_font=dict(size=11, color=EPM_RED),
                                  tickfont=dict(size=10, color=EPM_RED),
                                  gridcolor="rgba(255,255,255,0.05)")
                    _wb_y2 = dict(showgrid=False)

                else:  # Comparativa β
                    # G3: filtrar presets que coincidan con β actual (±0.05) para evitar duplicados
                    _beta_presets_ref = [
                        (0.5, EPM_TEAL,  "β=0.5  mort. infantil", "dash"),
                        (1.0, EPM_BLUE,  "β=1.0  exponencial",    "dot"),
                        (3.5, EPM_AMBER, "β=3.5  desgaste mod.",  "dash"),
                        (5.0, EPM_RED,   "β=5.0  desgaste sev.",  "dot"),
                    ]
                    _beta_sets = [
                        (bv, bc, bn, bd) for bv, bc, bn, bd in _beta_presets_ref
                        if abs(bv - _dn_beta) > 0.05
                    ] + [(_dn_beta, EPM_GREEN, f"β={_dn_beta:.1f}  actual", "solid")]
                    for _bv, _bc, _bn, _bd in _beta_sets:
                        _pof_b = _weibull_pof(_t_arr, _bv, _dn_eta)
                        _fig_wb.add_trace(go.Scatter(
                            x=_t_arr, y=_pof_b, mode="lines",
                            line=dict(color=_bc,
                                      width=3.0 if abs(_bv - _dn_beta) < 0.01 else 1.5,
                                      dash=_bd),
                            name=_bn,
                            hovertemplate=f"t=%{{x:.1f}}a  PoF=%{{y:.1%}}<extra>{_bn}</extra>",
                        ), secondary_y=False)
                    _fig_wb.add_vline(x=_dn_age,
                        line=dict(color="rgba(255,255,255,0.35)", width=1.5, dash="dash"),
                        annotation_text=f"  Hoy: {_dn_age:.0f}a",
                        annotation_font=dict(size=10, color="#CBD5E1"),
                        annotation_position="top right")
                    st.caption(
                        f"η = {_dn_eta:.0f} años fijo. La curva actual (β={_dn_beta:.1f}) "
                        f"se resalta en verde. Calibra β con benchmarks normativos de la industria."
                    )
                    _wb_y1 = dict(title="Probabilidad de Falla PoF",
                                  title_font=dict(size=11),
                                  tickformat=".0%", range=[0, 1.05],
                                  gridcolor="rgba(255,255,255,0.05)")
                    _wb_y2 = dict(showgrid=False)

                # ── Layout común ──────────────────────────────────────────────
                _fig_wb.update_layout(
                    height=410,
                    margin=dict(l=10, r=10, t=30, b=10),
                    legend=dict(
                        orientation="h", yanchor="bottom", y=1.02,
                        xanchor="left", x=0,
                        font=dict(size=9),
                        bgcolor="rgba(0,0,0,0)",
                        itemsizing="constant",
                    ),
                    plot_bgcolor="rgba(15,23,42,0.65)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    hovermode="x unified",
                    xaxis=dict(
                        title="Tiempo (años)",
                        title_font=dict(size=11),
                        gridcolor="rgba(255,255,255,0.05)",
                        tickfont=dict(size=10),
                        range=[0, _t_max],
                        zeroline=False,
                    ),
                    yaxis=_wb_y1,
                    yaxis2=_wb_y2,
                )

                # Zoom habilitado
                st.plotly_chart(_fig_wb, use_container_width=True,
                                config={"scrollZoom": True, "displayModeBar": "hover",
                                        "modeBarButtonsToRemove": ["select2d", "lasso2d"],
                                        "toImageButtonOptions": {"scale": 2}})

                # ── Recuadro de conclusión — actualiza en tiempo real ─────────
                # G2: texto de fase alineado con el régimen Weibull real
                _wb_phase_txt = {
                    "decelerating":      "β < 1 — mortalidad infantil: hazard decreciente, "
                                         "riesgo mayor en los primeros años de vida.",
                    "constant":          "β ≈ 1 — fallas aleatorias: hazard constante "
                                         "(modelo exponencial), sin envejecimiento diferencial.",
                    "accelerating":      "Fase de aceleración — el ritmo de falla aún está "
                                         "aumentando (antes del pico de falla Mode).",
                    "decelerating_wear": "Fase de desaceleración — el activo superó el pico "
                                         "de densidad de falla (Mode); la tasa decrece.",
                }.get(_wb_regime_phase, "")
                _wb_horizon_lines = []
                if _wb_to_b50 > 0.1:
                    _wb_horizon_lines.append(
                        f"⏱ Tiempo hasta B50: **{_wb_to_b50:.1f} años**")
                if _wb_to_mode is not None and _wb_to_mode > 0.1:
                    _wb_horizon_lines.append(
                        f"📍 Tiempo hasta Mode (pico de falla): **{_wb_to_mode:.1f} años**")
                if _wb_to_eta > 0.1:
                    _wb_horizon_lines.append(
                        f"⚙️ Tiempo hasta η (63.2% acum.): **{_wb_to_eta:.1f} años**")

                if _dn_b_enabled and _dn_vpe is not None and _dn_vpe > 0:
                    _wb_conclusion = (
                        f"**Componente A** (recurrente): EAL = **{_dn_eal_a:,.1f} MM COP/año** "
                        f"(PoF {_dn_pof:.1%} × CoF-A {_dn_cof:,.1f} MM). "
                        f"**Componente B** (evento único {_dn_cof_b:,.1f} MM): "
                        f"VPE = **{_dn_vpe:,.1f} MM COP** sobre {_dn_hz}a "
                        f"(WACC {_dn_wacc:.0%}) ≡ **{_dn_vpe_annual:,.1f} MM COP/año**. "
                        f"EAL total financiero: **{_dn_eal:,.1f} MM COP/año**."
                    )
                elif _age_eal > 0:
                    _wb_conclusion = (
                        f"Sin intervención, en **5 años** el EAL pasará de "
                        f"**{_age_eal:,.1f} MM COP** a **{_wb_eal_5yr:,.1f} MM COP** "
                        f"(+{_wb_eal_5yr - _age_eal:,.1f} MM), un incremento del "
                        f"**{(_wb_eal_5yr/_age_eal - 1)*100:.0f}%**."
                    )
                else:
                    _wb_conclusion = (
                        "Defina una Consecuencia de Falla (CoF) para proyectar el riesgo futuro."
                    )

                _wb_box_color = _wb_risk_color
                _wb_box_bg    = _wb_risk_bg
                _wb_horizons_html = "".join(
                    f"<span style='display:block;margin:2px 0;font-size:0.82rem;"
                    f"color:#CBD5E1;'>{ln}</span>"
                    for ln in _wb_horizon_lines
                ) if _wb_horizon_lines else ""

                st.markdown(
                    f"""
<div style="background:{_wb_box_bg};border:1px solid {_wb_box_color}44;
border-left:4px solid {_wb_box_color};border-radius:8px;
padding:12px 16px;margin-top:6px;">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
    <div>
      <span style="font-size:0.72rem;font-weight:700;letter-spacing:.08em;
      color:{_wb_box_color};text-transform:uppercase;">Riesgo {_wb_risk_zone}</span>
      &nbsp;
      <span style="font-size:0.82rem;color:#94a3b8;">
        PoF={_dn_pof:.1%} | {"EAL-A=" + f"{_dn_eal_a:,.1f} MM/año · EAL-B=" + f"{_dn_eal_b:,.1f} MM/año · Total=" + f"{_dn_eal:,.1f} MM/año" if (_dn_b_enabled and _dn_vpe) else "EAL=" + f"{_age_eal:,.1f} MM COP/año"}
      </span>
    </div>
    <div style="font-size:0.79rem;color:{_wb_box_color};font-weight:600;">
      ΔEAL/año ≈ {_wb_deal_yr:,.2f} MM COP
    </div>
  </div>
  <div style="margin-top:6px;font-size:0.82rem;color:#94a3b8;">{_wb_phase_txt}</div>
  {_wb_horizons_html}
  <div style="margin-top:8px;font-size:0.84rem;color:#e2e8f0;
  border-top:1px solid rgba(255,255,255,0.08);padding-top:8px;">
    {_wb_conclusion}
  </div>
</div>
""",
                    unsafe_allow_html=True,
                )

            else:
                # ── DRIVER-BASED / CONCEPT-RATING: Matriz CoF Copperleaf C55 ──
                st.markdown(
                    "**Matriz de Consecuencias de Falla** — selecciona el nivel L1–L5 "
                    "por tipo de impacto. El motor suma los valores monetizados de cada tipo.")
                st.caption(
                    "Este enfoque replica la experiencia de usuario de Copperleaf C55: "
                    "el analista ve las anclas descriptivas de cada celda y "
                    "puede diferenciar, por ejemplo, H&S=L4 con Ambiental=L1.")

                _ctm_cfg = epm_config.get("risk_matrix", {}).get("consequence_types_matrix", {})
                _cof_type_order = ["financiero", "hs", "ambiental", "regulatorio", "reputacional"]
                _cof_lvl_labels = {1:"L1 — Insignificante", 2:"L2 — Menor",
                                   3:"L3 — Moderada", 4:"L4 — Alta", 5:"L5 — Catastrófica"}

                _b_consequence_types: dict = {}
                _b_consequence_type_probs: dict = {}
                _cof_total_baseline = 0.0
                _eal_total_per_type = 0.0

                # Tabla de probabilidades (necesaria dentro del loop)
                _pl_cfg = epm_config.get("risk_matrix", {}).get("probability_levels", {})
                _pof_labels = {1:"P1-Rara",2:"P2-Baja",3:"P3-Posible",
                               4:"P4-Probable",5:"P5-Seguro"}

                for _cof_t in _cof_type_order:
                    _cof_tdata = _ctm_cfg.get(_cof_t, {})
                    if not _cof_tdata:
                        continue
                    _cof_icon  = _cof_tdata.get("icon", "")
                    _cof_label = _cof_tdata.get("label", _cof_t.capitalize())
                    _cof_desc  = _cof_tdata.get("description", "")
                    _cof_bu_lvls = _cof_tdata.get("levels_by_bu", {}).get(_w_bu, {})
                    if not _cof_bu_lvls:
                        _cof_bu_lvls = next(iter(_cof_tdata.get("levels_by_bu", {}).values()), {})

                    with st.expander(f"{_cof_icon} {_cof_label}", expanded=False):
                        st.caption(_cof_desc)
                        # ── Componente A: Recurrente / anual ──────────────────
                        st.markdown(
                            '<div style="font-size:0.70rem;font-weight:700;color:#0369A1;'
                            'margin-bottom:4px">🔁 Componente A — Recurrente</div>',
                            unsafe_allow_html=True)
                        _cof_col_sel, _cof_col_info = st.columns([1, 2], gap="medium")
                        with _cof_col_sel:
                            _cof_prev_a = st.session_state.get(f"wi_b_ct_{_cof_t}_a_{_wc_gen}", 3)
                            _cof_sel_a = st.select_slider(
                                f"Nivel-A {_cof_label}",
                                options=[1, 2, 3, 4, 5],
                                value=_cof_prev_a,
                                format_func=lambda x: _cof_lvl_labels[x],
                                key=f"wi_b_cof_{_cof_t}_a_inp_{_wc_gen}",
                                label_visibility="collapsed")
                            st.session_state[f"wi_b_ct_{_cof_t}_a_{_wc_gen}"] = _cof_sel_a
                            st.caption("PoF-A (recurrente)")
                            _pof_t_default = st.session_state.get(f"wi_b_rbp_{_wc_gen}", 3)
                            _pof_prev_a = st.session_state.get(
                                f"wi_b_pt_{_cof_t}_a_{_wc_gen}", _pof_t_default)
                            _pof_sel_a = st.select_slider(
                                f"PoF-A {_cof_label}",
                                options=[1, 2, 3, 4, 5],
                                value=_pof_prev_a,
                                format_func=lambda x: _pof_labels[x],
                                key=f"wi_b_pof_{_cof_t}_a_inp_{_wc_gen}",
                                label_visibility="collapsed")
                            st.session_state[f"wi_b_pt_{_cof_t}_a_{_wc_gen}"] = _pof_sel_a
                        with _cof_col_info:
                            _cof_cell_a = _cof_bu_lvls.get(str(_cof_sel_a), {})
                            _cof_val_a  = _cof_cell_a.get("value_COP", 0)
                            _cof_anch_a = _cof_cell_a.get("anchor", "—")
                            _pof_val_a  = _pl_cfg.get(str(_pof_sel_a), {}).get("annual_prob", 0)
                            _eal_a_mm   = _cof_val_a * _pof_val_a / 1e6
                            st.markdown(
                                f'<div style="background:#F0F9FF;border:1px solid #BAE6FD;'
                                f'border-radius:6px;padding:8px 12px">'
                                f'<div style="font-size:0.68rem;color:#0369A1;margin-bottom:2px">'
                                f'A-Recurrente · Valor monetizado</div>'
                                f'<div style="font-weight:700;color:#1E3A5F;font-size:0.9rem">'
                                f'{_cof_lvl_labels[_cof_sel_a]} · {_cof_val_a/1e6:,.0f} MM COP</div>'
                                f'<div style="font-size:0.70rem;color:#94A3B8;margin-top:4px">'
                                f'{_cof_anch_a}</div>'
                                f'<div style="font-size:0.70rem;color:#0369A1;'
                                f'margin-top:6px;font-weight:600">'
                                f'EAL-A: {_eal_a_mm:,.2f} MM COP/año</div>'
                                f'</div>',
                                unsafe_allow_html=True)

                        # ── Componente B: Evento único (opcional) ─────────────
                        _db_b_enabled = st.checkbox(
                            f"＋ Componente B — Evento único {_cof_label}",
                            value=False,
                            key=f"wi_b_{_cof_t}_b_enable_{_wc_gen}",
                            help=f"Habilita un componente de evento único para {_cof_label} "
                                 f"(ej: multa puntual, fallecimiento, sanción). "
                                 f"Nivel para la matriz = max(A, B); PoF = max(pof-A, pof-B).",
                        )
                        if _db_b_enabled:
                            st.markdown(
                                '<div style="font-size:0.70rem;font-weight:700;color:#7C3AED;'
                                'margin-bottom:4px">⚡ Componente B — Evento único</div>',
                                unsafe_allow_html=True)
                            _cof_col_b, _cof_info_b = st.columns([1, 2], gap="medium")
                            with _cof_col_b:
                                _cof_prev_b = st.session_state.get(f"wi_b_ct_{_cof_t}_b_{_wc_gen}", 1)
                                _cof_sel_b = st.select_slider(
                                    f"Nivel-B {_cof_label}",
                                    options=[1, 2, 3, 4, 5],
                                    value=_cof_prev_b,
                                    format_func=lambda x: _cof_lvl_labels[x],
                                    key=f"wi_b_cof_{_cof_t}_b_inp_{_wc_gen}",
                                    label_visibility="collapsed")
                                st.session_state[f"wi_b_ct_{_cof_t}_b_{_wc_gen}"] = _cof_sel_b
                                st.caption("PoF-B (único)")
                                _pof_prev_b = st.session_state.get(f"wi_b_pt_{_cof_t}_b_{_wc_gen}", 1)
                                _pof_sel_b = st.select_slider(
                                    f"PoF-B {_cof_label}",
                                    options=[1, 2, 3, 4, 5],
                                    value=_pof_prev_b,
                                    format_func=lambda x: _pof_labels[x],
                                    key=f"wi_b_pof_{_cof_t}_b_inp_{_wc_gen}",
                                    label_visibility="collapsed")
                                st.session_state[f"wi_b_pt_{_cof_t}_b_{_wc_gen}"] = _pof_sel_b
                            with _cof_info_b:
                                _cof_cell_b = _cof_bu_lvls.get(str(_cof_sel_b), {})
                                _cof_val_b  = _cof_cell_b.get("value_COP", 0)
                                _cof_anch_b = _cof_cell_b.get("anchor", "—")
                                _pof_val_b  = _pl_cfg.get(str(_pof_sel_b), {}).get("annual_prob", 0)
                                _eal_b_mm   = _cof_val_b * _pof_val_b / 1e6
                                st.markdown(
                                    f'<div style="background:#FDF4FF;border:1px solid #E9D5FF;'
                                    f'border-radius:6px;padding:8px 12px">'
                                    f'<div style="font-size:0.68rem;color:#7C3AED;'
                                    f'margin-bottom:2px">'
                                    f'B-Evento único · Valor monetizado</div>'
                                    f'<div style="font-weight:700;color:#1E3A5F;font-size:0.9rem">'
                                    f'{_cof_lvl_labels[_cof_sel_b]} · {_cof_val_b/1e6:,.0f} MM COP</div>'
                                    f'<div style="font-size:0.70rem;color:#94A3B8;margin-top:4px">'
                                    f'{_cof_anch_b}</div>'
                                    f'<div style="font-size:0.70rem;color:#7C3AED;'
                                    f'margin-top:6px;font-weight:600">'
                                    f'EAL-B: {_eal_b_mm:,.2f} MM COP/año</div>'
                                    f'</div>',
                                    unsafe_allow_html=True)
                        else:
                            _cof_sel_b = 1
                            _pof_sel_b = 1
                            _cof_val_b = 0
                            _pof_val_b = 0.0
                            _eal_b_mm  = 0.0

                        # Nivel para matriz = max(A, B); PoF = max(pof-A, pof-B)
                        _cof_sel = max(_cof_sel_a, _cof_sel_b)
                        _pof_sel = max(_pof_sel_a, _pof_sel_b)
                        _cof_val = max(_cof_val_a, _cof_val_b)  # nivel dominante para totales

                        # EAL combinado = A + B
                        _eal_t_ab_mm = _eal_a_mm + _eal_b_mm
                        if _db_b_enabled and _eal_b_mm > 0:
                            st.markdown(
                                f'<div style="background:#FFF7ED;border:1px solid #FCD34D;'
                                f'border-radius:6px;padding:6px 10px;font-size:0.72rem;'
                                f'color:#92400E;margin-top:6px">'
                                f'EAL-A {_eal_a_mm:,.2f} + EAL-B {_eal_b_mm:,.2f} = '
                                f'<b>EAL total {_eal_t_ab_mm:,.2f} MM COP/año</b> · '
                                f'Nivel matriz: {_cof_lvl_labels[_cof_sel]} × P{_pof_sel}'
                                f'</div>',
                                unsafe_allow_html=True)
                        _b_consequence_types[_cof_t] = _cof_sel
                        _b_consequence_type_probs[_cof_t] = _pof_sel
                        _cof_total_baseline += _cof_val
                        _eal_total_per_type += (_cof_val_a * _pof_val_a
                                                + _cof_val_b * _pof_val_b)

                # Probabilidad de falla compartida (fallback / referencia global)
                _brc_pcol, _brc_ocol = st.columns(2, gap="medium")
                with _brc_pcol:
                    st.markdown("**PoF global (referencia)**")
                    st.caption("Pre-llena PoF por tipo · cada tipo puede sobreescribirse arriba")
                    _b_rbp = st.select_slider("Probabilidad", options=[1,2,3,4,5],
                        value=3, key=f"wi_b_rbp_{_wc_gen}",
                        format_func=lambda x: {1:"1-Rara",2:"2-Baja",3:"3-Posible",
                                                4:"4-Probable",5:"5-Seguro"}[x])
                with _brc_ocol:
                    st.markdown("**Costo de inacción (OPEX correctivo)**")
                    st.caption("Mantenimiento correctivo, multas, pérdidas directas")
                    _b_opex = st.number_input("OPEX sin proyecto (MM COP/año)",
                        min_value=0.0, value=50.0,
                        step=10.0, key=f"wi_b_opex_{_wc_gen}", label_visibility="collapsed")

                # CoF combinado = suma de tipos; nivel ordinal = máximo de tipos (fallback)
                _b_rbc = max(_b_consequence_types.values()) if _b_consequence_types else 3
                _b_prob_val = _pl_cfg.get(str(_b_rbp), {}).get("annual_prob", 0)
                # EAL con PoF por tipo (Brecha A C55)
                _eal_baseline_mm = _eal_total_per_type / 1e6

                st.markdown(
                    f'<div style="background:#EFF6FF;border:1.5px solid #1E3A5F;'
                    f'border-radius:8px;padding:10px 16px;display:flex;gap:32px;'
                    f'align-items:center;margin-top:8px">'
                    f'<div><div style="font-size:0.65rem;color:#94A3B8">CoF total (Σ tipos)</div>'
                    f'<div style="font-weight:800;color:#1E3A5F;font-size:1.1rem">'
                    f'{_cof_total_baseline/1e6:,.0f} MM COP</div></div>'
                    f'<div><div style="font-size:0.65rem;color:#94A3B8">PoF global ref.</div>'
                    f'<div style="font-weight:800;color:#1E3A5F;font-size:1.1rem">'
                    f'P{_b_rbp} ({_b_prob_val:.2%}/año)</div></div>'
                    f'<div><div style="font-size:0.65rem;color:#94A3B8">EAL baseline (Σ tipo×PoF)</div>'
                    f'<div style="font-weight:800;color:#C0392B;font-size:1.1rem">'
                    f'{_eal_baseline_mm:,.1f} MM COP/año</div></div>'
                    f'</div>',
                    unsafe_allow_html=True)

                # ── Mapa de Calor Riesgo Baseline — posición en matriz 5×5 ───────
                if _b_consequence_types and _b_consequence_type_probs:
                    with st.expander(
                        "🗺️ Mapa de Calor — Posición Baseline en Matriz C55 5×5",
                        expanded=True):
                        st.caption(
                            "Cada tipo de consecuencia ocupa una celda de la matriz "
                            "Consecuencia (C1-C5) × Probabilidad (P1-P5). "
                            "Las alternativas dibujarán flechas desde estos puntos "
                            "hasta sus posiciones residuales.")
                        _fig_rm_bl = _build_risk_matrix_fig(
                            bl_types=_b_consequence_types,
                            bl_probs=_b_consequence_type_probs,
                            ctm_config=_ctm_cfg,
                            title="Riesgo Baseline — Do Nothing",
                        )
                        st.plotly_chart(_fig_rm_bl, use_container_width=True,
                                        config={"displayModeBar": False})

                _dn_pof = None; _dn_beta = None; _dn_eta = None
                _dn_age = None; _dn_cof  = None; _dn_opex = _b_opex

            # ── BRECHA DE CAPACIDAD — solo Crecimiento / Expansión ────────────
            if _w_inv == "Crecimiento / Expansión":
                with st.expander(
                    "📈 Análisis de Brecha de Capacidad — Demanda vs. Oferta disponible",
                    expanded=True):
                    st.caption(
                        "Cuantifica cuándo la demanda superará la capacidad disponible "
                        "y monetiza la brecha como Energía No Suministrada (ENS). "
                        "El resultado pre-llena el driver **VD-3 (Cobertura)** en FASE B.")

                    _gcA, _gcB = st.columns(2, gap="large")
                    with _gcA:
                        st.markdown("**📦 Capacidad del sistema**")
                        _gap_unit = st.selectbox(
                            "Unidad de capacidad",
                            ["MW", "MVA", "m³/s", "Clientes (miles)"],
                            key="wi_gap_unit")
                        _gap_cap = st.number_input(
                            f"Capacidad actual ({_gap_unit})",
                            min_value=0.1,
                            value=float(st.session_state.get("wi_gap_cap_v", 100.0)),
                            step=1.0, key="wi_gap_cap",
                            help="Capacidad instalada o contratada hoy")
                        _gap_deg = st.slider(
                            "Degradación de capacidad (%/año)",
                            0.0, 10.0,
                            float(st.session_state.get("wi_gap_deg_v", 1.5)),
                            step=0.1, key="wi_gap_deg",
                            help="La capacidad útil decrece con envejecimiento o derating")
                        _gap_shadow = st.number_input(
                            f"Precio sombra (MM COP / {_gap_unit}·año)",
                            min_value=0.0,
                            value=float(st.session_state.get("wi_gap_shadow_v", 50.0)),
                            step=5.0, key="wi_gap_shadow",
                            help="VALI regulatorio o precio mercado por unidad de capacidad no atendida. "
                                 "Para T&D eléctrico: use precio XM + VENS (≈ 335 COP/kWh = 335 MM COP/GWh)")
                    with _gcB:
                        st.markdown("**📈 Demanda proyectada**")
                        _gap_dem = st.number_input(
                            f"Demanda actual ({_gap_unit})",
                            min_value=0.1,
                            value=float(st.session_state.get("wi_gap_dem_v", 80.0)),
                            step=1.0, key="wi_gap_dem",
                            help="Carga o demanda actual del sistema (punto de partida)")
                        _gap_growth = st.slider(
                            "Tasa de crecimiento de demanda (%/año)",
                            0.0, 15.0,
                            float(st.session_state.get("wi_gap_growth_v", 3.5)),
                            step=0.1, key="wi_gap_growth",
                            help="Proyección regulatoria o histórica de crecimiento de carga")
                        _gap_horz = st.number_input(
                            "Horizonte de análisis (años)",
                            min_value=5, max_value=30,
                            value=int(st.session_state.get(
                                "wi_gap_horz_v",
                                cfg_e.get("analysis_horizon_years", 15))),
                            step=1, key="wi_gap_horz")

                    # ── Cálculo de curvas ──────────────────────────────────────
                    _gh = int(_gap_horz)
                    _gy_list  = list(range(_w_year, _w_year + _gh + 1))
                    _cap_crv  = [_gap_cap  * ((1 - _gap_deg  / 100) ** t) for t in range(_gh + 1)]
                    _dem_crv  = [_gap_dem  * ((1 + _gap_growth / 100) ** t) for t in range(_gh + 1)]
                    _gap_crv  = [max(0.0, d - c) for d, c in zip(_dem_crv, _cap_crv)]

                    # Primer año de cruce (demanda > capacidad)
                    _gap_yr   = next((y for y, (d, c) in zip(_gy_list, zip(_dem_crv, _cap_crv))
                                      if d > c), None)

                    # ENS acumulada (unidad·año) y monetización
                    _ens_units = sum(_gap_crv)                            # unidad·año
                    _ens_mm    = _ens_units * _gap_shadow                 # MM COP total
                    # ENS anual promedio desde el año de cruce (para sugerencia VD-3)
                    if _gap_yr:
                        _gi_cross = _gy_list.index(_gap_yr)
                        _ens_post = _gap_crv[_gi_cross:]
                        _ens_ann_mm = (sum(_ens_post) / max(1, len(_ens_post))) * _gap_shadow
                    else:
                        _ens_ann_mm = 0.0

                    # ── Gráfico Plotly: área + curvas ─────────────────────────
                    _fig_gap = go.Figure()
                    # Área de brecha (relleno entre curvas)
                    _fig_gap.add_trace(go.Scatter(
                        x=_gy_list, y=_cap_crv,
                        name=f"Capacidad ({_gap_unit})",
                        line=dict(color=EPM_DARK, width=2.5),
                        mode="lines+markers", marker=dict(size=4),
                        fill=None))
                    _fig_gap.add_trace(go.Scatter(
                        x=_gy_list, y=_dem_crv,
                        name=f"Demanda ({_gap_unit})",
                        line=dict(color=EPM_RED, width=2.5),
                        mode="lines+markers", marker=dict(size=4),
                        fill="tonexty",
                        fillcolor="rgba(220,38,38,0.10)"))
                    # Zona de brecha (solo positiva) en ámbar
                    _dem_clip = [d if d > c else c
                                 for d, c in zip(_dem_crv, _cap_crv)]
                    _fig_gap.add_trace(go.Scatter(
                        x=_gy_list, y=_dem_clip,
                        name="Zona de brecha",
                        line=dict(color="rgba(0,0,0,0)", width=0),
                        fill="tonexty",
                        fillcolor="rgba(217,119,6,0.22)",
                        showlegend=True))
                    if _gap_yr:
                        _fig_gap.add_vline(
                            x=_gap_yr, line_dash="dash",
                            line_color=EPM_AMBER, line_width=2.0,
                            annotation_text=f"⚠ Cruce: {_gap_yr}",
                            annotation_position="top right",
                            annotation_font_color=EPM_AMBER,
                            annotation_bgcolor="#FFFBEB")
                    _fig_gap.update_layout(
                        height=270, margin=dict(t=10, b=40, l=55, r=10),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(15,23,42,0.6)",
                        font=dict(family="Inter,Arial", size=11, color=EPM_SLATE),
                        xaxis=dict(title="Año", dtick=2, gridcolor="#F1F5F9"),
                        yaxis=dict(title=_gap_unit, gridcolor="#F1F5F9"),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                                    xanchor="right", x=1))
                    st.plotly_chart(_fig_gap, use_container_width=True,
                                    config={"displayModeBar": False})

                    # ── KPI tiles ─────────────────────────────────────────────
                    _gk1, _gk2, _gk3, _gk4 = st.columns(4, gap="small")
                    _gyr_txt = str(_gap_yr) if _gap_yr else "Sin cruce"
                    _gyr_col = EPM_RED if _gap_yr else EPM_DARK
                    with _gk1:
                        st.markdown(
                            f'<div style="background:{_gyr_col}12;border:1.5px solid {_gyr_col};'
                            f'border-radius:8px;padding:8px;text-align:center">'
                            f'<div style="font-size:0.60rem;font-weight:700;color:{_gyr_col};'
                            f'text-transform:uppercase">Año de cruce</div>'
                            f'<div style="font-size:1.25rem;font-weight:900;color:{_gyr_col}">'
                            f'{_gyr_txt}</div></div>', unsafe_allow_html=True)
                    with _gk2:
                        _gap_max = max(_gap_crv) if _gap_crv else 0
                        st.markdown(
                            f'<div style="background:#FFF7ED;border:1.5px solid {EPM_AMBER};'
                            f'border-radius:8px;padding:8px;text-align:center">'
                            f'<div style="font-size:0.60rem;font-weight:700;color:{EPM_AMBER};'
                            f'text-transform:uppercase">Brecha máx.</div>'
                            f'<div style="font-size:1.25rem;font-weight:900;color:{EPM_AMBER}">'
                            f'{_gap_max:,.1f} {_gap_unit}</div></div>', unsafe_allow_html=True)
                    with _gk3:
                        st.markdown(
                            f'<div style="background:#F0FDF4;border:1.5px solid {EPM_DARK};'
                            f'border-radius:8px;padding:8px;text-align:center">'
                            f'<div style="font-size:0.60rem;font-weight:700;color:{EPM_DARK};'
                            f'text-transform:uppercase">ENS acum. ({_gap_unit}·a)</div>'
                            f'<div style="font-size:1.25rem;font-weight:900;color:{EPM_DARK}">'
                            f'{_ens_units:,.1f}</div></div>', unsafe_allow_html=True)
                    with _gk4:
                        st.markdown(
                            f'<div style="background:#EFF6FF;border:1.5px solid {EPM_BLUE};'
                            f'border-radius:8px;padding:8px;text-align:center">'
                            f'<div style="font-size:0.60rem;font-weight:700;color:{EPM_BLUE};'
                            f'text-transform:uppercase">ENS monetizada</div>'
                            f'<div style="font-size:1.25rem;font-weight:900;color:{EPM_BLUE}">'
                            f'{_ens_mm:,.1f} MM</div></div>', unsafe_allow_html=True)

                    # Persistir en session state para pre-fill FASE B
                    st.session_state.update({
                        "w_gap_computed":  True,
                        "w_gap_year":      _gap_yr or (_w_year + _gh),
                        "w_ens_mm":        _ens_mm,
                        "w_ens_annual_mm": _ens_ann_mm,
                        "w_gap_units":     _gap_unit,
                        "w_gap_shadow":    _gap_shadow,
                    })

                    if _gap_yr:
                        _delay_gap = _gap_yr - _w_year
                        _urg_gap = ("🔴 AHORA" if _delay_gap <= 1
                                    else "🟡 PRONTO" if _delay_gap <= 3
                                    else "🟢 FLEXIBLE")
                        st.info(
                            f"{_urg_gap} — Cruce en **{_gap_yr}** "
                            f"({_delay_gap} año{'s' if _delay_gap != 1 else ''} desde hoy). "
                            f"ENS total: **{_ens_mm:,.1f} MM COP**. "
                            f"En FASE B, el valor anual promedio "
                            f"(**{_ens_ann_mm:,.1f} MM COP/año**) se ofrecerá como sugerencia "
                            f"para el driver **VD-3 (Cobertura)**.")
                    else:
                        st.success(
                            "✅ No se proyecta cruce en el horizonte de análisis. "
                            "La capacidad disponible cubre la demanda en todo el período.")
            else:
                st.session_state["w_gap_computed"] = False

            # ════════════════════════════════════════════════════════════════
            # CONSOLIDADO DE CONSECUENCIAS — vista previa antes de confirmar
            # Disponible en AMBOS modos (asset_aware y driver_based).
            # Permite al analista ver la magnitud relativa de cada tipo antes
            # de cerrar el Do Nothing y avanzar a las alternativas.
            # ════════════════════════════════════════════════════════════════
            _cons_data_chart = locals().get("_b_consequence_types", {})
            _cons_prob_chart  = locals().get("_b_consequence_type_probs", {})

            if _cons_data_chart:
                _ctm_c   = epm_config.get("risk_matrix", {}).get("consequence_types_matrix", {})
                _pl_c    = epm_config.get("risk_matrix", {}).get("probability_levels", {})
                _wacc_c  = float(epm_config.get("wacc", 0.10))
                _hz_c    = int(epm_config.get("analysis_horizon_years", 10))
                _npvf_c  = ((1 - (1 + _wacc_c) ** -_hz_c) / _wacc_c
                            if _wacc_c > 0 else float(_hz_c))

                # Para asset_aware: financiero viene del input real, no del config
                _dn_cof_real = locals().get("_dn_cof", None)   # None en driver_based
                _dn_pof_real = locals().get("_dn_pof", None)   # None en driver_based
                _dn_eal_real = locals().get("_dn_eal", None)   # None en driver_based

                _type_order_c = ["financiero", "hs", "ambiental", "regulatorio", "reputacional"]
                _lvl_colors_c = {1:"#22C55E", 2:"#84CC16", 3:"#F59E0B", 4:"#EF4444", 5:"#7F1D1D"}
                _lvl_text_c   = {1:"L1", 2:"L2", 3:"L3", 4:"L4", 5:"L5"}

                # ── Recolectar datos por tipo ─────────────────────────────────
                _rows_c = []
                for _ct_c in _type_order_c:
                    if _ct_c not in _cons_data_chart:
                        continue
                    _lvl_c  = _cons_data_chart[_ct_c]
                    _prob_c = _cons_prob_chart.get(_ct_c, 3)
                    _tdata_c = _ctm_c.get(_ct_c, {})
                    _lbl_c   = _tdata_c.get("label", _ct_c.capitalize())
                    _icon_c  = _tdata_c.get("icon", "")
                    _bu_lvls_c = _tdata_c.get("levels_by_bu", {}).get(_w_bu, {})
                    if not _bu_lvls_c:
                        _bu_lvls_c = next(iter(_tdata_c.get("levels_by_bu", {}).values()), {})

                    # CoF en MM COP
                    if _ct_c == "financiero" and _dn_cof_real is not None:
                        _cof_mm_c = float(_dn_cof_real)
                    else:
                        _cof_mm_c = _bu_lvls_c.get(str(_lvl_c), {}).get("value_COP", 0) / 1e6

                    # EAL anual en MM COP
                    if _ct_c == "financiero" and _dn_eal_real is not None:
                        _eal_mm_c = float(_dn_eal_real)
                    else:
                        _ann_p_c  = _pl_c.get(str(_prob_c), {}).get("annual_prob", 0.0)
                        _eal_mm_c = _cof_mm_c * _ann_p_c

                    _anchor_c = _bu_lvls_c.get(str(_lvl_c), {}).get("anchor", "")
                    _rows_c.append({
                        "tipo":   _ct_c,
                        "label":  f"{_icon_c} {_lbl_c}",
                        "nivel":  _lvl_c,
                        "prob":   _prob_c,
                        "cof_mm": _cof_mm_c,
                        "eal_mm": _eal_mm_c,
                        "color":  _lvl_colors_c.get(_lvl_c, "#94A3B8"),
                        "anchor": _anchor_c,
                    })

                # Ordenar por EAL desc
                _rows_c.sort(key=lambda r: r["eal_mm"], reverse=True)
                _total_eal_c  = sum(r["eal_mm"] for r in _rows_c)
                _total_cof_c  = max(r["cof_mm"] for r in _rows_c) if _rows_c else 0.0
                _npv_inaccion = _total_eal_c * _npvf_c
                _n_types_c    = len(_rows_c)

                st.markdown("---")
                st.markdown(
                    f'<div style="background:rgba(15,23,42,0.6);border-radius:10px;'
                    f'border:1px solid rgba(255,255,255,0.07);'
                    f'padding:10px 18px;margin-bottom:4px;'
                    f'display:flex;align-items:center;gap:12px">'
                    f'<span style="color:#6EE7B7;font-size:0.68rem;font-weight:700;'
                    f'text-transform:uppercase;letter-spacing:0.08em;flex-shrink:0">'
                    f'Consolidado de Consecuencias — Do Nothing</span>'
                    f'<span style="color:#94A3B8;font-size:0.72rem">'
                    f'{_n_types_c} tipo{"s" if _n_types_c > 1 else ""} de impacto</span>'
                    f'<span style="margin-left:auto;background:rgba(0,168,107,0.15);'
                    f'color:#6EE7B7;font-size:0.68rem;font-weight:700;'
                    f'padding:3px 10px;border-radius:6px;border:1px solid rgba(0,168,107,0.3);'
                    f'font-family:\'JetBrains Mono\',monospace;flex-shrink:0">'
                    f'EAL Total {_total_eal_c:,.1f} MM/año</span>'
                    f'</div>',
                    unsafe_allow_html=True)

                # ── Paleta por TIPO (constante, independiente del nivel) ──────
                _type_colors_c = {
                    "financiero":   "#F59E0B",   # amber    — riesgo financiero
                    "hs":           "#EF4444",   # red      — salud y seguridad
                    "ambiental":    "#22C55E",   # green    — impacto ambiental
                    "regulatorio":  "#3B82F6",   # blue     — cumplimiento
                    "reputacional": "#A855F7",   # purple   — reputación
                }
                _lvl_badge_c = {
                    1: "#22C55E", 2: "#84CC16", 3: "#F59E0B",
                    4: "#EF4444", 5: "#7F1D1D",
                }

                # ── Mini-cards: una por tipo (CoF + EAL + nivel) ─────────────
                _cards_html = (
                    '<div style="display:flex;gap:8px;margin:12px 0 6px 0;'
                    'flex-wrap:wrap;">'
                )
                for _r in _rows_c:
                    _tc  = _type_colors_c.get(_r["tipo"], "#94A3B8")
                    _lc  = _lvl_badge_c.get(_r["nivel"], "#94A3B8")
                    _pct = (_r["eal_mm"] / _total_eal_c * 100
                            if _total_eal_c > 0 else 0)
                    _cards_html += (
                        f'<div style="flex:1;min-width:130px;max-width:220px;'
                        f'background:rgba(30,41,59,0.78);'
                        f'border:1px solid rgba(255,255,255,0.08);'
                        f'border-left:3px solid {_tc};'
                        f'border-radius:8px;padding:10px 12px;">'
                        # Fila: badge nivel + nombre tipo
                        f'<div style="display:flex;align-items:center;gap:5px;'
                        f'margin-bottom:7px;overflow:hidden">'
                        f'<span style="background:{_lc}22;color:{_lc};'
                        f'font-size:0.58rem;font-weight:700;padding:2px 6px;'
                        f'border-radius:3px;flex-shrink:0">L{_r["nivel"]}</span>'
                        f'<span style="font-size:0.60rem;color:#94A3B8;font-weight:600;'
                        f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'
                        f'line-height:1.2">{_r["label"]}</span></div>'
                        # EAL — valor principal
                        f'<div style="font-size:1.1rem;font-weight:800;color:{_tc};'
                        f'font-family:\'JetBrains Mono\',monospace;'
                        f'letter-spacing:-0.02em;line-height:1">'
                        f'{_r["eal_mm"]:,.1f}</div>'
                        f'<div style="font-size:0.58rem;color:#94A3B8;'
                        f'margin-top:1px;font-weight:500">MM COP/año · EAL</div>'
                        # CoF + porcentaje
                        f'<div style="font-size:0.60rem;color:#94A3B8;'
                        f'margin-top:6px;padding-top:6px;'
                        f'border-top:1px solid rgba(255,255,255,0.06)">'
                        f'CoF {_r["cof_mm"]:,.0f} MM &nbsp;·&nbsp; '
                        f'<span style="color:{_tc};font-weight:700">'
                        f'{_pct:.0f}%</span> del EAL total</div>'
                        f'</div>'
                    )
                _cards_html += '</div>'
                st.markdown(_cards_html, unsafe_allow_html=True)

                # ── Gráfico único: EAL horizontal, dark, sin duplicación ──────
                _bar_colors_c = [
                    _type_colors_c.get(r["tipo"], "#94A3B8") for r in _rows_c
                ]
                _fig_c = go.Figure()
                _fig_c.add_trace(go.Bar(
                    y=[r["label"] for r in _rows_c],
                    x=[r["eal_mm"] for r in _rows_c],
                    orientation="h",
                    marker=dict(
                        color=_bar_colors_c,
                        opacity=0.88,
                        line=dict(color="rgba(255,255,255,0.12)", width=1),
                    ),
                    text=[f"<b>{r['eal_mm']:,.1f}</b>" for r in _rows_c],
                    textposition="outside",
                    textfont=dict(color="#CBD5E1", size=11,
                                  family="JetBrains Mono, monospace"),
                    cliponaxis=False,
                    customdata=[
                        [r["cof_mm"], f"P{r['prob']}", r["nivel"], r["anchor"]]
                        for r in _rows_c
                    ],
                    hovertemplate=(
                        "<b>%{y}</b><br>"
                        "EAL: <b>%{x:,.1f} MM COP/año</b><br>"
                        "CoF: %{customdata[0]:,.0f} MM COP / evento<br>"
                        "Nivel: L%{customdata[2]} · Prob: %{customdata[1]}<br>"
                        "<i style='color:#94A3B8'>%{customdata[3]}</i>"
                        "<extra></extra>"
                    ),
                    showlegend=False,
                ))

                # Línea de referencia: EAL total
                if _total_eal_c > 0:
                    _fig_c.add_vline(
                        x=_total_eal_c, line_dash="dot",
                        line_color="rgba(0,168,107,0.55)", line_width=1.5,
                        annotation_text=f" Total {_total_eal_c:,.1f} MM/año",
                        annotation_font_size=9,
                        annotation_font_color="#6EE7B7",
                        annotation_position="top right",
                    )

                _row_h = max(52, 240 // max(_n_types_c, 1))
                _fig_c.update_layout(
                    height=max(200, 48 + _n_types_c * _row_h),
                    margin=dict(t=8, b=28, l=10, r=110),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(15,23,42,0.0)",
                    font=dict(family="Inter, Arial, sans-serif",
                              size=11, color="#94A3B8"),
                    xaxis=dict(
                        title=dict(
                            text="Pérdida Anual Esperada — EAL  (MM COP/año)",
                            font=dict(size=10, color="#94A3B8"),
                        ),
                        showgrid=True,
                        gridcolor="rgba(255,255,255,0.06)",
                        zeroline=True,
                        zerolinecolor="rgba(255,255,255,0.12)",
                        tickfont=dict(size=10, color="#94A3B8"),
                        color="#94A3B8",
                    ),
                    yaxis=dict(
                        autorange="reversed",
                        showgrid=False,
                        tickfont=dict(size=11, color="#CBD5E1"),
                        color="#CBD5E1",
                    ),
                    hoverlabel=dict(
                        bgcolor="#1E293B",
                        bordercolor="rgba(255,255,255,0.15)",
                        font=dict(color="#E2E8F0", size=12),
                    ),
                )

                st.plotly_chart(_fig_c, use_container_width=True,
                                config={"displayModeBar": False})

                # ── KPIs de cierre ────────────────────────────────────────────
                _kc1, _kc2, _kc3, _kc4 = st.columns(4)
                with _kc1:
                    _dom = _rows_c[0] if _rows_c else {}
                    _dom_pct = (_dom.get("eal_mm", 0) / _total_eal_c * 100
                                if _total_eal_c > 0 else 0)
                    st.metric(
                        "EAL total anual",
                        f"{_total_eal_c:,.1f} MM/año",
                        help="Pérdida Anual Esperada consolidada (Σ CoF×PoF por tipo)")
                with _kc2:
                    st.metric(
                        "NPV de la inacción",
                        f"{_npv_inaccion:,.0f} MM COP",
                        help=f"EAL × factor NPV a {_hz_c} años (WACC {_wacc_c:.0%}). "
                             f"Costo presente de no intervenir durante el horizonte.")
                with _kc3:
                    st.metric(
                        f"Tipo dominante",
                        _dom.get("label", "—").strip(),
                        delta=f"{_dom_pct:.0f}% del EAL",
                        delta_color="off",
                        help="Tipo de consecuencia que más EAL aporta")
                with _kc4:
                    _max_cof_row = max(_rows_c, key=lambda r: r["cof_mm"]) if _rows_c else {}
                    st.metric(
                        "Mayor consecuencia",
                        f"{_max_cof_row.get('cof_mm', 0):,.0f} MM COP",
                        delta=_max_cof_row.get("label", "").strip(),
                        delta_color="off",
                        help="Peor escenario por evento de falla (CoF máximo entre tipos)")

                st.caption(
                    f"📌 Confirma este Do Nothing si los valores reflejan correctamente "
                    f"el riesgo actual del activo. Una vez confirmado, podrás ingresar "
                    f"las {_w_alt_cnt} alternativas de intervención.")

            # ── Fin consolidado ───────────────────────────────────────────────
            st.divider()

            def _build_current_risk_input(_ct_snap, _ctp_snap):
                _aw = (_w_meth == "asset_aware")
                # Cada componente asset_aware guarda SU propia curva Weibull
                # (β/η/edad) y su CoF exacto — capturados de los widgets vivos de
                # ESTE componente (keyed por `_wc_gen`), no de un slot compartido.
                # El motor ya suma el ΔR Weibull componente a componente
                # (epm_core `_evaluate_inner` / `_optimal_timing_inner`), y FASE B
                # suma la EAL evitada de todos vía `_asset_aware_eal_avoided_COP`.
                return RiskInput(
                    baseline_consequence_level=_b_rbc,
                    baseline_probability_level=_b_rbp,
                    residual_consequence_level=_b_rbc,
                    residual_probability_level=_b_rbp,
                    consequence_types=_ct_snap,
                    residual_consequence_types=_ct_snap,
                    consequence_type_probs=_ctp_snap,
                    residual_consequence_type_probs=_ctp_snap,
                    label=_wc_current_label,
                    asset_type=st.session_state.get("w_asset_family", "") if _aw else "",
                    weibull_beta=float(_dn_beta) if (_aw and _dn_beta) else 0.0,
                    weibull_eta=float(_dn_eta) if (_aw and _dn_eta) else 0.0,
                    asset_age_years=float(_dn_age) if (_aw and _dn_age is not None) else 0.0,
                    weibull_cof_COP=(float(_dn_cof) * 1_000_000.0) if (_aw and _dn_cof) else 0.0,
                )

            _cb4a, _cm4a, _cn4a = st.columns(3)
            with _cb4a:
                if st.button("← Volver", use_container_width=True, key="wi_back4a"):
                    st.session_state["wizard_step"] = 2
                    st.rerun()
            with _cm4a:
                if st.button("+ Guardar y agregar otro componente", use_container_width=True,
                             key="wi_add_another_comp"):
                    st.session_state["w_risk_components"].append({
                        "label": _wc_current_label or f"Componente {len(st.session_state['w_risk_components']) + 1}",
                        "risk_input": _build_current_risk_input(
                            locals().get("_b_consequence_types", {}),
                            locals().get("_b_consequence_type_probs", {}),
                        ),
                    })
                    st.session_state["_wc_clear_label_pending"] = True
                    # Incrementar la generación de keys de widgets: el próximo
                    # componente usa keys nunca vistas por el frontend, forzando un
                    # remount limpio de los select_slider/checkbox/number_input de
                    # la captura de riesgo (ver comentario junto a `_wc_gen` arriba).
                    st.session_state["_wc_key_gen"] = _wc_gen + 1
                    st.toast(
                        "Componente guardado. Formulario limpio para el siguiente.",
                        icon="✅")
                    st.rerun()
            with _cn4a:
                if st.button("Confirmar Componentes de Inacción →", type="primary",
                             use_container_width=True, key="wi_set_base"):
                    _final_components = list(st.session_state["w_risk_components"])
                    _final_components.append({
                        "label": _wc_current_label or f"Componente {len(_final_components) + 1}",
                        "risk_input": _build_current_risk_input(
                            locals().get("_b_consequence_types", {}),
                            locals().get("_b_consequence_type_probs", {}),
                        ),
                    })

                    _ts_base = datetime.now().strftime("%H%M%S")
                    _base_proj = ProjectInput(
                        id=f"BASE-{_w_year}-{_ts_base}",
                        name=f"Do Nothing — {_w_name}",
                        business_unit=_w_bu,
                        investment_type=_w_inv,
                        asset_type=st.session_state.get("w_baseline_asset_type", ""),
                        is_baseline=True,
                        exclusion_group_id=f"DUEL-{_w_year}-{_ts_base}",
                        valuation_methodology=_w_meth,
                        problem_description=st.session_state.get("w_problem_desc", ""),
                        costs=CostInput(
                            capex_COP=0.0,
                            opex_incremental_COP_year=_b_opex * 1_000_000,
                        ),
                        execution_year=_w_year,
                        optimal_year=_w_year,
                        drivers=[],
                        risk_components=[c["risk_input"] for c in _final_components],
                        analysis_horizon_years=st.session_state.get("w_analysis_horizon_years") or 0,
                        construction_years=0,
                    )
                    # Capturar snapshot de tipos y probs para FASE B
                    st.session_state["_b_consequence_types_snap"] = locals().get(
                        "_b_consequence_types", {})
                    st.session_state["_b_consequence_type_probs_snap"] = locals().get(
                        "_b_consequence_type_probs", {})
                    st.session_state["wizard_baseline_proj"] = _base_proj
                    st.session_state["w_duel_group_id"]     = _base_proj.exclusion_group_id
                    st.session_state["w_risk_components"]   = []  # limpiar para el próximo proyecto
                    st.session_state["_wc_clear_label_pending"] = True
                    # Guardar baseline Weibull para uso en FASE B. Ahora la
                    # física por componente vive en `_base_proj.risk_components`
                    # (cada RiskInput con su weibull_beta/eta/asset_age_years/
                    # weibull_cof_COP). `w_aw_baseline` queda sólo como resumen
                    # del COMPONENTE PRINCIPAL (índice 0) para las tarjetas y el
                    # encabezado — antes tomaba el ÚLTIMO componente declarado,
                    # que no tenía relación con cuál era el principal.
                    if _w_meth == "asset_aware":
                        _c0 = _final_components[0]["risk_input"]
                        _c0_pof = _weibull_pof_at_age(
                            _c0.weibull_beta, _c0.weibull_eta, _c0.asset_age_years)
                        st.session_state["w_aw_baseline"] = {
                            "beta":   _c0.weibull_beta,
                            "eta":    _c0.weibull_eta,
                            "age":    _c0.asset_age_years,
                            "cof_mm": _c0.weibull_cof_COP / 1_000_000.0,
                            "pof":    _c0_pof,
                            "opex_mm": _dn_opex,
                        }
                    st.rerun()

        # ── FASE B: Ingresar cada alternativa ─────────────────────────────────
        elif _w_base_proj is not None and _w_cur_idx < _w_alt_cnt and st.session_state.get("wizard_results") is None:
            _letter = chr(65 + _w_cur_idx)

            # ── D-Task18: migración de `.risk` (eliminado) a `.risk_components` ──
            # El primer componente guardado en FASE A hace de "componente
            # principal" para la UI detallada de Bloque 2/3 (matriz Copperleaf
            # completa, tarjetas Weibull). Componentes adicionales (índice 1+)
            # reciben una declaración residual simplificada más abajo (Bloque 2b).
            #
            # D-Task17/18 → corrección multi-componente asset_aware: cada
            # componente lleva AHORA su propia curva Weibull en su RiskInput
            # (weibull_beta/eta/asset_age_years/weibull_cof_COP, poblados por
            # `_build_current_risk_input` en FASE A). El motor ya suma el ΔR
            # Weibull componente a componente, y el beneficio en pesos (EAL
            # evitada) que alimenta el VNT se calcula abajo con
            # `_asset_aware_eal_avoided_COP(_base_components, ...)`, que recorre
            # TODOS los componentes — ya no un único `w_aw_baseline`.
            _base_components = list(_w_base_proj.risk_components) or [RiskInput(
                baseline_consequence_level=1, baseline_probability_level=1)]
            _base_comp0 = _base_components[0]
            _residuals_ok = True   # bloquea "Agregar Alternativa" si falta declarar algo

            # Encabezado de fase
            _aw_bl = st.session_state.get("w_aw_baseline", {})
            if _w_meth == "asset_aware" and _aw_bl:
                _bl_summary = (
                    f'PoF actual: {_aw_bl.get("pof",0):.1%} · '
                    f'EAL: {_aw_bl.get("pof",0)*_aw_bl.get("cof_mm",0):,.0f} MM COP/año · '
                    f'β={_aw_bl.get("beta","—")} η={_aw_bl.get("eta","—")}a '
                    f'edad={_aw_bl.get("age","—")}a')
                if len(_base_components) > 1:
                    _bl_summary += (f' · componente 1 de {len(_base_components)} '
                                    f'(cada uno con curva propia)')
            else:
                _b_rbc_h = _base_comp0.baseline_consequence_level
                _b_rbp_h = _base_comp0.baseline_probability_level
                _bl_summary = f'Baseline riesgo: C={_b_rbc_h} × P={_b_rbp_h} = {_b_rbc_h*_b_rbp_h} pts'
                if len(_base_components) > 1:
                    _bl_summary += f' · +{len(_base_components)-1} componente(s) adicionales'
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">'
                f'<span style="background:{EPM_DARK};color:#fff;font-weight:700;'
                f'font-size:0.78rem;padding:3px 12px;border-radius:20px">Alt. {_letter}</span>'
                f'<span style="font-size:1.05rem;font-weight:700;color:{EPM_DEEP}">'
                f'Intervención {_w_cur_idx + 1} de {_w_alt_cnt}</span>'
                f'<span style="margin-left:auto;font-size:0.72rem;color:{EPM_GRAY}">'
                f'{_bl_summary}</span>'
                f'</div>',
                unsafe_allow_html=True)

            # ── Bloque 1: Datos generales de la alternativa ────────────────────
            st.markdown("##### 📋 Datos de la intervención")
            _dc1, _dc2, _dc3 = st.columns([3, 1.5, 1], gap="medium")
            with _dc1:
                _d_name = st.text_input(
                    "Nombre / descripción *",
                    value=st.session_state.get(f"w_alt_{_letter}_name", f"Alternativa {_letter}"),
                    key=f"wi_alt_{_letter}_name_inp",
                    placeholder=f"Ej: Reemplazo transformador principal — Alt. {_letter}")
            with _dc2:
                _d_capex = st.number_input(
                    "CAPEX (MM COP)",
                    min_value=0.0, max_value=5_000_000.0,
                    value=float(st.session_state.get(f"w_alt_{_letter}_capex", 0.0)),
                    step=100.0, key=f"wi_alt_{_letter}_capex_inp")
            with _dc3:
                _prev_constr = st.session_state.get(f"w_alt_{_letter}_constr", 1)
                _d_constr = st.selectbox(
                    "Años constr.", [1, 2, 3, 4, 5],
                    index=_prev_constr - 1, key=f"wi_alt_{_letter}_constr_inp")

            # ── Base del estimativo — incertidumbre propia de ESTA alternativa ──
            # No se pregunta "qué tan preciso es" (nadie lo sabe) sino "de dónde
            # salió el número", que un ingeniero de proyectos responde sin dudar.
            # La precisión se deduce de la fuente.
            _unc_cfg   = cfg_e.get("cost_estimate_uncertainty", {})
            _unc_bands = _unc_cfg.get("classes", {})
            _unc_def   = _unc_cfg.get("default_class", "Class5")
            _ec_keys   = ["Class1", "Class2", "Class3", "Class4", "Class5"]
            _ec_prev   = st.session_state.get(f"w_alt_{_letter}_estclass", _unc_def)

            _ec1, _ec2 = st.columns([3, 3], gap="medium")
            with _ec1:
                _d_estclass = st.selectbox(
                    "Base del estimativo de CAPEX *",
                    _ec_keys,
                    index=_ec_keys.index(_ec_prev) if _ec_prev in _ec_keys else 4,
                    format_func=lambda k: _unc_bands.get(k, {}).get("label", k),
                    key=f"wi_alt_{_letter}_estclass_inp",
                    help=(
                        "Sobre qué se construyó la cifra de CAPEX. Determina la banda de "
                        "incertidumbre de ESTA alternativa — no del mundo, sino de qué tan "
                        "bien se conoce este número.\n\n"
                        "Es lo que decide si la comparación entre alternativas está resuelta: "
                        "la incertidumbre común (mercado, precios) las mueve a todas junto y "
                        "se cancela; la de estimación las mueve por separado.\n\n"
                        "Por defecto se asume la base más débil — declarar un buen estimativo "
                        "es lo que da ventaja, no omitir la declaración."
                    ),
                )
            with _ec2:
                _ecb = _unc_bands.get(_d_estclass, {})
                if _ecb and _d_capex > 0:
                    _cx_lo = _d_capex * (1 + _ecb.get("low", 0.0))
                    _cx_hi = _d_capex * (1 + _ecb.get("high", 0.0))
                    st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
                    st.caption(
                        f"Rango implícito: **{_cx_lo:,.0f}** ← {_d_capex:,.0f} → "
                        f"**{_cx_hi:,.0f}** MM COP "
                        f"({_ecb.get('low',0)*100:+.0f}% / {_ecb.get('high',0)*100:+.0f}%). "
                        f"{_ecb.get('description','')}"
                    )
                elif _ecb:
                    st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
                    st.caption(
                        f"Banda {_ecb.get('low',0)*100:+.0f}% / {_ecb.get('high',0)*100:+.0f}%. "
                        f"{_ecb.get('description','')}"
                    )

            _do1, _do2 = st.columns([3, 3], gap="medium")
            with _do1:
                _d_opex = st.number_input(
                    "OPEX incremental (MM COP/año)",
                    min_value=-50_000.0, max_value=50_000.0,
                    value=float(st.session_state.get(f"w_alt_{_letter}_opex", 0.0)),
                    step=10.0, key=f"wi_alt_{_letter}_opex_inp",
                    help="OPEX CON este proyecto menos OPEX SIN proyecto. "
                         "Negativo = ahorro operativo (ej: menos mantenimiento correctivo). "
                         "Positivo = costo operativo adicional (ej: nuevo personal, licencias).")
            with _do2:
                _opex_ref = st.session_state.get("w_aw_baseline", {}).get("opex_mm", 0.0)
                if _opex_ref > 0:
                    st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
                    st.caption(
                        f"Referencia: OPEX correctivo Do Nothing = **{_opex_ref:,.1f} MM/año**. "
                        f"Si esta alternativa elimina ese costo, ingrese **−{_opex_ref:,.1f}**.")

            # ── Tipo de activo (determina horizonte de evaluación) ────────────
            _ahbu = cfg_e.get("asset_horizons_by_bu", {}).get(_w_bu, {})
            _at_keys = sorted(k for k in _ahbu if k != "_default")
            _at_labels = {k: f"{k}  ({_ahbu[k]}a)" for k in _at_keys}
            _at_default_h = _ahbu.get("_default", cfg_e.get("analysis_horizon_years", 10))
            _prev_at = st.session_state.get(f"w_alt_{_letter}_asset_type", "")
            _prev_at_idx = (_at_keys.index(_prev_at) + 1) if _prev_at in _at_keys else 0
            _dat1, _dat2 = st.columns([3, 1], gap="medium")
            with _dat1:
                _at_options = ["— Genérico (horizonte BU por defecto)"] + [_at_labels[k] for k in _at_keys]
                _at_sel_idx = st.selectbox(
                    "Tipo de activo",
                    range(len(_at_options)),
                    format_func=lambda i: _at_options[i],
                    index=_prev_at_idx,
                    key=f"wi_alt_{_letter}_asset_type_inp",
                    help="Determina el horizonte de evaluación según IEEE/IEC/CREG. "
                         "Sin selección usa el horizonte por defecto de la BU.")
                _d_asset_type = _at_keys[_at_sel_idx - 1] if _at_sel_idx > 0 else ""
            with _dat2:
                _h_used = (_ahbu.get(_d_asset_type, _at_default_h)
                           if _d_asset_type else _at_default_h)
                st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
                st.caption(f"Horizonte: **{_h_used} años**")

            # Safe defaults para variables asset_aware (definidas en Bloque 3)
            # Evita NameError si el bloque no se ejecutó aún (ej: RULE_BASED)
            _int_type   = "reposicion"
            _aw_age_new = 0.0
            _pof_new    = 0.0
            _delta_pof  = 0.0
            _aw_eal_mm  = 0.0

            # ── Bloque 2: Riesgo residual ──────────────────────────────────────
            st.markdown("##### 🛡️ Riesgo residual (con esta alternativa aplicada)")
            st.caption(
                "Componente principal — declaración detallada. "
                "Sin valor por defecto: cada campo debe declararse explícitamente."
            )
            _b_rbc_v = _base_comp0.baseline_consequence_level
            _b_rbp_v = _base_comp0.baseline_probability_level

            if _w_meth == "asset_aware":
                # ── ASSET-AWARE: Financiero derivado de Weibull + tipos adicionales manuales
                # _d_rrc y _d_rrp: niveles financieros Weibull (calculados en Bloque 3)
                _d_rrc = locals().get("_d_rrc", _b_rbc_v)
                _d_rrp = locals().get("_d_rrp", max(1, _b_rbp_v - 1))
                _pof_new_disp = locals().get("_pof_new", 0.0)

                _rr_score = _d_rrc * _d_rrp
                _rr_delta = _b_rbc_v * _b_rbp_v - _rr_score
                _rr_color = EPM_DARK if _rr_delta > 0 else (EPM_AMBER if _rr_delta == 0 else EPM_RED)

                # Tarjeta de resumen Weibull (financiero)
                st.markdown(
                    f'<div style="background:{_rr_color}15;border:1.5px solid {_rr_color};'
                    f'border-radius:8px;padding:10px 14px;display:flex;gap:24px;'
                    f'align-items:center;margin-bottom:8px">'
                    f'<div><div style="font-size:0.62rem;color:{EPM_GRAY};font-weight:600;'
                    f'text-transform:uppercase">Riesgo Financiero (Weibull)</div>'
                    f'<div style="font-size:1.2rem;font-weight:800;color:{_rr_color}">'
                    f'C{_d_rrc}×P{_d_rrp} = {_rr_score} pts</div></div>'
                    f'<div><div style="font-size:0.62rem;color:{EPM_GRAY}">PoF post-intervención</div>'
                    f'<div style="font-size:1.1rem;font-weight:800;color:{EPM_DARK}">'
                    f'{_pof_new_disp:.1%}</div></div>'
                    f'<div style="margin-left:auto;font-size:0.72rem;color:{_rr_color};font-weight:600">'
                    f'{"↓ −"+str(abs(_rr_delta))+" pts vs. baseline" if _rr_delta > 0 else "→ Sin cambio"}'
                    f'</div></div>',
                    unsafe_allow_html=True)

                # Inicializar riesgo residual con el tipo financiero Weibull
                _d_residual_consequence_types:       dict = {"financiero": _d_rrc}
                _d_residual_consequence_type_probs:  dict = {"financiero": _d_rrp}

                # Recuperar tipos adicionales que el analista ingresó en Do Nothing
                # (unión de todos los componentes guardados en FASE A — D-Task18)
                _bl_ctypes_aw: dict = {}
                _bl_ctypes_probs_aw: dict = {}
                for _bc_aw in _base_components:
                    _bl_ctypes_aw.update(getattr(_bc_aw, "consequence_types", {}) or {})
                    _bl_ctypes_probs_aw.update(getattr(_bc_aw, "consequence_type_probs", {}) or {})
                _ctm_cfg_b = epm_config.get("risk_matrix", {}).get("consequence_types_matrix", {})
                _supp_types_b = ["hs", "ambiental", "regulatorio", "reputacional"]
                _cof_lvl_labels_b = {1:"L1 — Insignificante", 2:"L2 — Menor",
                                     3:"L3 — Moderada", 4:"L4 — Alta", 5:"L5 — Catastrófica"}
                _pof_labels_b = {1:"P1-Rara", 2:"P2-Baja", 3:"P3-Posible",
                                 4:"P4-Probable", 5:"P5-Seguro"}

                # Tipos presentes en el baseline (los que el analista ingresó en FASE A)
                _supp_active = [t for t in _supp_types_b if t in _bl_ctypes_aw]

                if _supp_active:
                    with st.expander(
                        "🛡️ Riesgo residual — H&S · Ambiental · Regulatorio · Reputacional",
                        expanded=True,
                    ):
                        st.caption(
                            "Declara el riesgo residual de cada tipo adicional "
                            "después de aplicar esta intervención. "
                            "Sin valor por defecto — un tipo sin declarar bloquea "
                            "el avance de esta alternativa.")
                        _pl_cfg_b = epm_config.get("risk_matrix", {}).get("probability_levels", {})
                        for _cof_t_b in _supp_active:
                            _cof_tdata_b  = _ctm_cfg_b.get(_cof_t_b, {})
                            _cof_label_b  = _cof_tdata_b.get("label", _cof_t_b.capitalize())
                            _cof_icon_b   = _cof_tdata_b.get("icon", "")
                            _cof_bu_lvls_b = _cof_tdata_b.get("levels_by_bu", {}).get(_w_bu, {})
                            if not _cof_bu_lvls_b:
                                _cof_bu_lvls_b = next(
                                    iter(_cof_tdata_b.get("levels_by_bu", {}).values()), {})
                            _bl_lvl_b  = _bl_ctypes_aw.get(_cof_t_b, 3)
                            _bl_pof_b  = _bl_ctypes_probs_aw.get(_cof_t_b, 3)
                            _rr_prev_b  = st.session_state.get(f"wi_b_{_letter}_rct_{_cof_t_b}")
                            _rr_pof_prev_b = st.session_state.get(f"wi_b_{_letter}_rpof_{_cof_t_b}")
                            _rb1, _rb2, _rb3 = st.columns([1, 1, 2], gap="small")
                            with _rb1:
                                _rr_sel_b = st.select_slider(
                                    f"{_cof_icon_b} {_cof_label_b}",
                                    options=[None, 1, 2, 3, 4, 5],
                                    value=_rr_prev_b,
                                    format_func=lambda x: _cof_lvl_labels_b[x] if x is not None else "— Sin declarar —",
                                    key=f"wi_b_{_letter}_rct_{_cof_t_b}_inp",
                                )
                                st.session_state[f"wi_b_{_letter}_rct_{_cof_t_b}"] = _rr_sel_b
                            with _rb2:
                                _rr_pof_sel_b = st.select_slider(
                                    f"PoF residual {_cof_label_b}",
                                    options=[None, 1, 2, 3, 4, 5],
                                    value=_rr_pof_prev_b,
                                    format_func=lambda x: _pof_labels_b[x] if x is not None else "— Sin declarar —",
                                    key=f"wi_b_{_letter}_rpof_{_cof_t_b}_inp",
                                )
                                st.session_state[f"wi_b_{_letter}_rpof_{_cof_t_b}"] = _rr_pof_sel_b
                            with _rb3:
                                if _rr_sel_b is None or _rr_pof_sel_b is None:
                                    _residuals_ok = False
                                    st.markdown(
                                        '<div style="background:#FEF2F2;border:1px solid #FCA5A5;'
                                        'border-radius:6px;padding:6px 10px;margin-top:20px;'
                                        'font-size:0.75rem;color:#B91C1C">'
                                        '⚠️ Falta declarar este tipo</div>',
                                        unsafe_allow_html=True)
                                else:
                                    _rr_val_b  = _cof_bu_lvls_b.get(str(_rr_sel_b), {}).get("value_COP", 0) / 1e6
                                    _bl_val_b  = _cof_bu_lvls_b.get(str(_bl_lvl_b), {}).get("value_COP", 0) / 1e6
                                    _delta_b   = _bl_val_b - _rr_val_b
                                    _d_col_b   = EPM_DARK if _delta_b > 0 else (EPM_AMBER if _delta_b == 0 else EPM_RED)
                                    _bl_prob_b = _pl_cfg_b.get(str(_bl_pof_b), {}).get("annual_prob", 0)
                                    _rr_prob_b = _pl_cfg_b.get(str(_rr_pof_sel_b), {}).get("annual_prob", 0)
                                    _delta_eal_b = (_bl_val_b * _bl_prob_b - _rr_val_b * _rr_prob_b)
                                    st.markdown(
                                        f'<div style="background:#F8FAFC;border:1px solid #E2E8F0;'
                                        f'border-radius:6px;padding:6px 10px;margin-top:20px;'
                                        f'font-size:0.75rem;color:#94A3B8">'
                                        f'<b>{_cof_label_b}</b>: {_bl_lvl_b}→{_rr_sel_b} · '
                                        f'<span style="color:{_d_col_b};font-weight:600">'
                                        f'ΔEAL: {"+" if _delta_eal_b>=0 else ""}'
                                        f'{_delta_eal_b:,.1f} MM/año</span></div>',
                                        unsafe_allow_html=True)
                            if _rr_sel_b is not None and _rr_pof_sel_b is not None:
                                _d_residual_consequence_types[_cof_t_b]      = _rr_sel_b
                                _d_residual_consequence_type_probs[_cof_t_b] = _rr_pof_sel_b

                # Nota: la matriz completa (Do Nothing → Residual) se despliega
                # en la sección "Tipo de Intervención" (Bloque 3), una vez que
                # Weibull calcula la PoF post-intervención real.
                st.caption(
                    "La matriz de movimiento de riesgo se actualizará "
                    "en la sección siguiente al definir el tipo de intervención.")
            else:
                # ── DRIVER-BASED / CONCEPT-RATING: Matriz CoF residual Copperleaf C55 ──
                # Componente principal (índice 0) — declaración detallada por tipo.
                _bl_ctypes = getattr(_base_comp0, "consequence_types", {})
                _ctm_cfg_b = epm_config.get("risk_matrix", {}).get("consequence_types_matrix", {})
                _cof_type_order_b = ["financiero", "hs", "ambiental", "regulatorio", "reputacional"]
                _cof_lvl_labels_b = {1:"L1 — Insignificante", 2:"L2 — Menor",
                                     3:"L3 — Moderada", 4:"L4 — Alta", 5:"L5 — Catastrófica"}

                st.markdown(
                    "**Riesgo residual — Matriz de Consecuencias** · "
                    "Indica el nivel que permanece *después* de ejecutar esta alternativa.")
                st.caption(
                    "Compara con el baseline (Do Nothing). "
                    "La reducción en cada tipo monetiza el beneficio de riesgo evitado.")

                _d_residual_consequence_types: dict = {}
                _d_residual_consequence_type_probs: dict = {}
                _cof_total_residual = 0.0
                _cof_total_bl_sum   = 0.0
                _eal_total_residual_per_type = 0.0
                _eal_total_bl_per_type = 0.0

                # Tabla de probabilidades para cálculo per-tipo
                _pl_cfg_b = epm_config.get("risk_matrix", {}).get("probability_levels", {})
                _pof_labels_b = {1:"P1-Rara",2:"P2-Baja",3:"P3-Posible",
                                 4:"P4-Probable",5:"P5-Seguro"}
                # PoF baseline por tipo (del snapshot de FASE A)
                _bl_ctypes_probs = getattr(_base_comp0, "consequence_type_probs", {})

                for _cof_t_b in _cof_type_order_b:
                    _cof_tdata_b = _ctm_cfg_b.get(_cof_t_b, {})
                    if not _cof_tdata_b:
                        continue
                    _cof_icon_b  = _cof_tdata_b.get("icon", "")
                    _cof_label_b = _cof_tdata_b.get("label", _cof_t_b.capitalize())
                    _cof_bu_lvls_b = _cof_tdata_b.get("levels_by_bu", {}).get(_w_bu, {})
                    if not _cof_bu_lvls_b:
                        _cof_bu_lvls_b = next(iter(
                            _cof_tdata_b.get("levels_by_bu", {}).values()), {})

                    # Nivel baseline de este tipo (del Do Nothing)
                    _bl_lvl_b = _bl_ctypes.get(_cof_t_b, _b_rbc_v)
                    _bl_val_b = _cof_bu_lvls_b.get(str(_bl_lvl_b), {}).get("value_COP", 0)
                    _bl_pof_t = _bl_ctypes_probs.get(_cof_t_b, _b_rbp_v)
                    _bl_prob_t = _pl_cfg_b.get(str(_bl_pof_t), {}).get("annual_prob", 0)
                    _cof_total_bl_sum += _bl_val_b
                    _eal_total_bl_per_type += _bl_val_b * _bl_prob_t

                    with st.expander(f"{_cof_icon_b} {_cof_label_b}", expanded=False):
                        _rr_col_sel, _rr_col_info = st.columns([1, 2], gap="medium")
                        with _rr_col_sel:
                            # CoF residual level — sin default: debe declararse (D-Task18)
                            _rr_prev = st.session_state.get(f"wi_b_{_letter}_rct_{_cof_t_b}")
                            _rr_sel = st.select_slider(
                                f"Residual {_cof_label_b}",
                                options=[None, 1, 2, 3, 4, 5],
                                value=_rr_prev,
                                format_func=lambda x: _cof_lvl_labels_b[x] if x is not None else "— Sin declarar —",
                                key=f"wi_b_{_letter}_rct_{_cof_t_b}_inp",
                                label_visibility="collapsed")
                            st.session_state[f"wi_b_{_letter}_rct_{_cof_t_b}"] = _rr_sel
                            # Brecha A C55: PoF residual independiente por tipo
                            st.caption("PoF residual (este tipo) — obligatorio")
                            _rr_pof_prev = st.session_state.get(f"wi_b_{_letter}_rpof_{_cof_t_b}")
                            _rr_pof_sel = st.select_slider(
                                f"PoF residual {_cof_label_b}",
                                options=[None, 1, 2, 3, 4, 5],
                                value=_rr_pof_prev,
                                format_func=lambda x: _pof_labels_b[x] if x is not None else "— Sin declarar —",
                                key=f"wi_b_{_letter}_rpof_{_cof_t_b}_inp",
                                label_visibility="collapsed")
                            st.session_state[f"wi_b_{_letter}_rpof_{_cof_t_b}"] = _rr_pof_sel
                        with _rr_col_info:
                            if _rr_sel is None or _rr_pof_sel is None:
                                _residuals_ok = False
                                st.markdown(
                                    '<div style="background:#FEF2F2;border:1px solid #FCA5A5;'
                                    'border-radius:6px;padding:8px 12px;font-size:0.78rem;'
                                    'color:#B91C1C">⚠️ Falta declarar el residual de '
                                    f'{_cof_label_b} — bloquea el avance de esta alternativa.</div>',
                                    unsafe_allow_html=True)
                            else:
                                _rr_cell_b = _cof_bu_lvls_b.get(str(_rr_sel), {})
                                _rr_val_b  = _rr_cell_b.get("value_COP", 0)
                                _rr_anch_b = _rr_cell_b.get("anchor", "—")
                                _delta_t   = _bl_val_b - _rr_val_b
                                _delta_col = EPM_DARK if _delta_t > 0 else (
                                    EPM_AMBER if _delta_t == 0 else EPM_RED)
                                _rr_prob_t = _pl_cfg_b.get(str(_rr_pof_sel), {}).get("annual_prob", 0)
                                _eal_bl_t_mm   = _bl_val_b * _bl_prob_t / 1e6
                                _eal_rr_t_mm   = _rr_val_b * _rr_prob_t / 1e6
                                _delta_eal_t   = _eal_bl_t_mm - _eal_rr_t_mm
                                _delta_eal_col = EPM_DARK if _delta_eal_t > 0 else EPM_AMBER
                                st.markdown(
                                    f'<div style="background:#F8FAFC;border:1px solid #E2E8F0;'
                                    f'border-radius:6px;padding:8px 12px">'
                                    f'<div style="font-size:0.68rem;color:#94A3B8;margin-bottom:2px">'
                                    f'Baseline {_cof_lvl_labels_b[_bl_lvl_b]} → Residual {_cof_lvl_labels_b[_rr_sel]}</div>'
                                    f'<div style="font-weight:700;color:{_delta_col};font-size:0.9rem">'
                                    f'{"↓ Reducción " if _delta_t > 0 else ("→ Sin cambio" if _delta_t == 0 else "↑ Aumento ")}'
                                    f'{"" if _delta_t == 0 else f"{abs(_delta_t)/1e6:,.0f} MM COP"}'
                                    f'</div>'
                                    f'<div style="font-size:0.72rem;color:#94A3B8;margin-top:4px">'
                                    f'{_rr_anch_b}</div>'
                                    f'<div style="font-size:0.72rem;color:{_delta_eal_col};margin-top:6px;font-weight:600">'
                                    f'ΔEAL este tipo: {_delta_eal_t:,.2f} MM COP/año</div>'
                                    f'</div>',
                                    unsafe_allow_html=True)
                                _d_residual_consequence_types[_cof_t_b] = _rr_sel
                                _d_residual_consequence_type_probs[_cof_t_b] = _rr_pof_sel
                                _cof_total_residual += _rr_val_b
                                _eal_total_residual_per_type += _rr_val_b * _rr_prob_t

                # Probabilidad residual compartida (referencia / fallback motor)
                # Alimenta el RiskInput final si algún tipo no se declaró — D-Task18:
                # sin default, debe declararse explícitamente.
                _rrp_col, _ = st.columns([1, 1])
                with _rrp_col:
                    st.markdown("**PoF residual global (usada como fallback del motor)** — obligatorio")
                    st.caption("Referencia global — cada tipo puede tener su propia PoF arriba")
                    _d_rrp = st.select_slider(
                        "Probabilidad residual", options=[None, 1, 2, 3, 4, 5],
                        value=st.session_state.get(f"w_alt_{_letter}_rrp"),
                        format_func=lambda x: {1:"1-Rara",2:"2-Baja",3:"3-Posible",
                                               4:"4-Probable",5:"5-Seguro"}.get(x, "— Sin declarar —"),
                        key=f"wi_alt_{_letter}_rrp_inp")
                    if _d_rrp is None:
                        _residuals_ok = False
                        st.warning("Falta declarar la PoF residual global de referencia.")

                # Nivel ordinal residual = máximo de tipos seleccionados (fallback motor)
                _d_rrc = max(_d_residual_consequence_types.values()) if _d_residual_consequence_types else max(1, _b_rbc_v - 1)

                # ── Mapa de Calor Riesgo: Baseline → Residual ─────────────────
                # Copperleaf C55: matriz siempre visible (expanded=True) para cada alt.
                if _bl_ctypes and _d_residual_consequence_types:
                    st.markdown(f"##### 🗺️ Movimiento de Riesgo — Do Nothing → Alt. {_letter}")
                    st.caption(
                        "Círculos = posición baseline (Do Nothing) por tipo de consecuencia. "
                        "Flechas + cuadrados = posición residual con esta alternativa. "
                        "Verde < 6 pts · Ámbar 6–12 pts · Rojo > 12 pts.")
                    _bl_p_snap = getattr(_base_comp0, "consequence_type_probs", {})
                    _fig_rm_alt = _build_risk_matrix_fig(
                        bl_types=_bl_ctypes,
                        bl_probs=_bl_p_snap,
                        ctm_config=_ctm_cfg_b,
                        title=f"Alt. {_letter} — Movimiento de Riesgo por Tipo",
                        alt_data=[{
                            "label": f"Alt {_letter}",
                            "color": EPM_DARK,
                            "res_types": _d_residual_consequence_types,
                            "res_probs": _d_residual_consequence_type_probs,
                        }],
                    )
                    st.plotly_chart(_fig_rm_alt, use_container_width=True,
                                    config={"displayModeBar": False})

                # Resumen ΔEAL con PoF per-tipo (Brecha A C55)
                _prob_val_b  = _pl_cfg_b.get(str(_d_rrp), {}).get("annual_prob", 0)
                _eal_bl_b    = _eal_total_bl_per_type / 1e6
                _eal_res_b   = _eal_total_residual_per_type / 1e6
                _delta_eal   = _eal_bl_b - _eal_res_b
                _rr_color    = EPM_DARK if _delta_eal > 0 else (
                    EPM_AMBER if _delta_eal == 0 else EPM_RED)

                st.markdown(
                    f'<div style="background:#EFF6FF;border:1.5px solid {_rr_color};'
                    f'border-radius:8px;padding:10px 16px;display:flex;gap:28px;'
                    f'align-items:center;margin-top:8px">'
                    f'<div><div style="font-size:0.65rem;color:#94A3B8">EAL baseline (Σ tipo×PoF)</div>'
                    f'<div style="font-weight:800;color:#1E3A5F;font-size:1.0rem">'
                    f'{_eal_bl_b:,.1f} MM COP/año</div></div>'
                    f'<div><div style="font-size:0.65rem;color:#94A3B8">EAL residual (Σ tipo×PoF)</div>'
                    f'<div style="font-weight:800;color:#1E3A5F;font-size:1.0rem">'
                    f'{_eal_res_b:,.1f} MM COP/año</div></div>'
                    f'<div><div style="font-size:0.65rem;color:#94A3B8">ΔEAL evitado/año</div>'
                    f'<div style="font-weight:800;color:{_rr_color};font-size:1.0rem">'
                    f'{"↓ " if _delta_eal > 0 else ("→ " if _delta_eal == 0 else "↑ ")}'
                    f'{abs(_delta_eal):,.1f} MM COP/año</div></div>'
                    f'</div>',
                    unsafe_allow_html=True)

            st.divider()

            # ── Bloque 2b: componentes adicionales (índice 1+) — D-Task18 ───────
            # Simplificación deliberada de alcance (ver reporte de la Tarea 18):
            # el componente principal (índice 0, arriba) recibe la declaración
            # detallada por tipo de consecuencia (matriz Copperleaf completa).
            # Los componentes adicionales reciben una declaración residual
            # global (una consecuencia + una probabilidad, o un costo anual si
            # son de modo directo) — sin desglose por tipo. Sigue siendo
            # genuinamente obligatoria y sin default: bloquea el avance si
            # falta declarar cualquiera de ellos.
            _extra_risk_inputs_alt = []
            if len(_base_components) > 1:
                st.markdown("##### 🛡️ Riesgo residual — componentes adicionales")
                st.caption(
                    "Declaración simplificada (sin desglose por tipo de consecuencia): "
                    "un valor residual global por componente. Obligatorio — sin valor "
                    "por defecto.")
                _cof_lvl_labels_extra = {1:"L1 — Insignificante", 2:"L2 — Menor",
                                          3:"L3 — Moderada", 4:"L4 — Alta", 5:"L5 — Catastrófica"}
                _pof_labels_extra = {1:"P1-Rara", 2:"P2-Baja", 3:"P3-Posible",
                                      4:"P4-Probable", 5:"P5-Seguro"}
                for _rc_idx, _base_comp_extra in enumerate(_base_components[1:], start=1):
                    _comp_label = _base_comp_extra.label or f"Componente {_rc_idx + 1}"
                    st.markdown(f"**{_comp_label}**")
                    if _base_comp_extra.baseline_annual_COP > 0:
                        _res_cop_prev = st.session_state.get(
                            f"wi_alt{_letter}_{_rc_idx}_res_dir")
                        _res_cop = st.number_input(
                            f"Costo anual residual tras Alt. {_letter} (MM COP)",
                            min_value=0.0,
                            value=_res_cop_prev,
                            key=f"wi_alt{_letter}_{_rc_idx}_res_dir_inp",
                            placeholder="Obligatorio — sin valor por defecto",
                        )
                        st.session_state[f"wi_alt{_letter}_{_rc_idx}_res_dir"] = _res_cop
                        if _res_cop is None:
                            _residuals_ok = False
                            st.warning(f"Falta declarar el costo residual de «{_comp_label}».")
                            continue
                        _extra_risk_inputs_alt.append(RiskInput(
                            baseline_consequence_level=1, baseline_probability_level=1,
                            baseline_annual_COP=_base_comp_extra.baseline_annual_COP,
                            residual_annual_COP=_res_cop * 1_000_000,
                            label=_base_comp_extra.label,
                            asset_type=_base_comp_extra.asset_type,
                            source_asset_id=_base_comp_extra.source_asset_id,
                        ))
                    else:
                        _rce1, _rce2 = st.columns(2, gap="medium")
                        with _rce1:
                            _res_cons_extra = st.select_slider(
                                f"Consecuencia residual tras Alt. {_letter} (1-5)",
                                options=[None, 1, 2, 3, 4, 5],
                                value=st.session_state.get(f"wi_alt{_letter}_{_rc_idx}_rc"),
                                format_func=lambda x: _cof_lvl_labels_extra[x] if x is not None else "— Sin declarar —",
                                key=f"wi_alt{_letter}_{_rc_idx}_rc_inp")
                            st.session_state[f"wi_alt{_letter}_{_rc_idx}_rc"] = _res_cons_extra
                        with _rce2:
                            _res_prob_extra = st.select_slider(
                                f"Probabilidad residual tras Alt. {_letter} (1-5)",
                                options=[None, 1, 2, 3, 4, 5],
                                value=st.session_state.get(f"wi_alt{_letter}_{_rc_idx}_rp"),
                                format_func=lambda x: _pof_labels_extra[x] if x is not None else "— Sin declarar —",
                                key=f"wi_alt{_letter}_{_rc_idx}_rp_inp")
                            st.session_state[f"wi_alt{_letter}_{_rc_idx}_rp"] = _res_prob_extra
                        if _res_cons_extra is None or _res_prob_extra is None:
                            _residuals_ok = False
                            st.warning(f"Falta declarar el residual de «{_comp_label}».")
                            continue
                        # Bugfix (code review, hallazgo de doble conteo): ver
                        # docstring de _build_extra_component_risk_input — en
                        # asset_aware, un componente adicional con curva Weibull
                        # propia se neutraliza (residual == baseline) para que
                        # su beneficio financiero fluya SOLO por el driver
                        # EAL-evitada (_asset_aware_eal_avoided_COP), nunca
                        # también por el ΔR propio del motor. El valor
                        # declarado por el analista queda igual en
                        # session_state (arriba) por si se necesita para
                        # display; solo cambia lo que se envía al motor
                        # (D-14, hallazgo de code review).
                        _extra_risk_inputs_alt.append(_build_extra_component_risk_input(
                            _base_comp_extra, _w_meth,
                            residual_cons_declared=_res_cons_extra,
                            residual_prob_declared=_res_prob_extra,
                        ))
                st.divider()

            if not _residuals_ok:
                st.warning(
                    "⚠️ Faltan valores residuales por declarar arriba — completa "
                    "todos los componentes antes de continuar. No hay valor por "
                    "defecto: cada campo debe declararse explícitamente.")

            # ── Bloque 3: Beneficios / Criterios (condicional por model_level) ─
            _prev_drv = st.session_state.get(f"w_alt_{_letter}_drivers", {})
            _drv_new  = {}
            _has_val  = False
            _checklist_results: dict = {}  # solo RULE_BASED

            if _w_model_level == "RULE_BASED":
                # ── RULE_BASED: gate cuantitativo ────────────────────────────
                st.markdown("##### 🔬 Gate de aprobación — RULE_BASED")

                # Umbrales desde config (con defaults seguros)
                _rb_cfg       = epm_config.get("rule_based_gate", {})
                _rb_pof_crit  = float(_rb_cfg.get("pof_critico",  0.50))   # hard gate
                _rb_pof_mod   = float(_rb_cfg.get("pof_moderado", 0.15))   # soft gate
                _rb_eal_thr   = float(_rb_cfg.get("eal_local_mm", 500.0))  # MM COP umbral local

                # Datos Weibull del activo (disponibles si es asset_aware)
                _aw_bl_rb     = st.session_state.get("w_aw_baseline", {})
                _rb_age       = float(_aw_bl_rb.get("age",     0.0))
                _rb_eta       = float(_aw_bl_rb.get("eta",     1.0))
                _rb_opex      = float(_aw_bl_rb.get("opex_mm", 0.0))
                _rb_cof       = float(_aw_bl_rb.get("cof_mm",  0.0))
                # D-Task17/18: el gate mira TODOS los componentes asset_aware.
                # EAL actual = Σ PoF_i × CoF_i (exposición total sin intervenir);
                # PoF del gate = el peor componente (worst-case para el trigger).
                _rb_eal_now_COP, _rb_desg = _asset_aware_eal_avoided_COP(
                    _base_components, "reposicion")
                if _rb_desg:
                    _rb_pof_now = max(_d["pof_now"] for _d in _rb_desg)
                    _rb_eal_now = _rb_eal_now_COP / 1_000_000.0   # MM COP EAL actual (todos)
                else:
                    _rb_pof_now = float(_aw_bl_rb.get("pof", 0.0))
                    _rb_eal_now = _rb_pof_now * _rb_cof
                _rb_capex_ref = float(st.session_state.get("w_capex_ref", 0.0))

                # ── Flag regulatorio (único input manual que se preserva) ──────
                _rb_reg = st.checkbox(
                    "⚖️ Inversión mandatoria por regulación, seguridad o norma técnica vigente",
                    value=st.session_state.get(f"wi_rb_{_letter}_reg", False),
                    key=f"wi_rb_{_letter}_reg",
                    help="CREG, RETIE, norma ambiental, orden judicial. "
                         "Activa aprobación automática sin importar PoF/EAL.")

                st.markdown("---")

                # ── Evaluación de gates ──────────────────────────────────────
                # Hard gate 1 — regulatorio (mandatorio)
                _rb_gate_reg = _rb_reg

                # Hard gate 2 — riesgo crítico cuantificado
                _rb_gate_crit = (_rb_pof_now >= _rb_pof_crit) and (_rb_eal_now >= _rb_eal_thr)

                # Soft gate — riesgo moderado
                _rb_opex_ratio = (_rb_opex / _rb_cof) if _rb_cof > 0 else 0.0
                _rb_gate_soft  = (
                    (_rb_pof_now >= _rb_pof_mod) and
                    ((_rb_eal_now >= _rb_eal_thr * 0.3) or (_rb_opex_ratio >= 0.30))
                )

                # Resultado final
                _rb_approved  = _rb_gate_reg or _rb_gate_crit or _rb_gate_soft

                # Guardar en _checklist_results para compatibilidad con snapshot
                _checklist_results = {
                    "gate_regulatorio":  _rb_gate_reg,
                    "gate_critico":      _rb_gate_crit,
                    "gate_moderado":     _rb_gate_soft,
                    "pof_evaluado":      _rb_pof_now,
                    "eal_evaluado_mm":   _rb_eal_now,
                    "opex_ratio":        _rb_opex_ratio,
                    "aprobado":          _rb_approved,
                }

                # ── Visualización de gates ────────────────────────────────────
                _rb_gc1, _rb_gc2, _rb_gc3 = st.columns(3, gap="small")

                def _gate_card(col, label, active, detail):
                    _icon  = "✅" if active else "○"
                    _bg    = "#052e16" if active else "#0f172a"
                    _bord  = "#16a34a44" if active else "#475569"
                    _col_t = "#4ade80" if active else "#94a3b8"
                    col.markdown(
                        f'<div style="background:{_bg};border:1px solid {_bord};'
                        f'border-radius:8px;padding:10px 12px;text-align:center;">'
                        f'<div style="font-size:1.2rem;">{_icon}</div>'
                        f'<div style="font-size:0.74rem;font-weight:700;color:{_col_t};'
                        f'margin:4px 0;">{label}</div>'
                        f'<div style="font-size:0.70rem;color:#94a3b8;">{detail}</div>'
                        f'</div>',
                        unsafe_allow_html=True)

                _gate_card(_rb_gc1, "MANDATORIO",
                           _rb_gate_reg,
                           "Obligación regulatoria / seguridad")
                _gate_card(_rb_gc2, "RIESGO CRÍTICO",
                           _rb_gate_crit,
                           f"PoF≥{_rb_pof_crit:.0%} y EAL≥{_rb_eal_thr:,.0f} MM")
                _gate_card(_rb_gc3, "RIESGO MODERADO",
                           _rb_gate_soft,
                           f"PoF≥{_rb_pof_mod:.0%} + EAL o OPEX>30% CoF")

                # Métricas del activo
                st.markdown("<div style='margin-top:10px;'></div>",
                            unsafe_allow_html=True)
                _rb_m1, _rb_m2, _rb_m3, _rb_m4 = st.columns(4, gap="small")
                _rb_m1.metric("PoF actual",  f"{_rb_pof_now:.1%}")
                _rb_m2.metric("EAL actual",  f"{_rb_eal_now:,.1f} MM")
                _rb_m3.metric("OPEX/CoF",    f"{_rb_opex_ratio:.0%}")
                _rb_m4.metric("CAPEX proy.", f"{_rb_capex_ref:,.0f} MM")

                st.markdown("<div style='margin-top:8px;'></div>",
                            unsafe_allow_html=True)

                # Veredicto principal
                if _rb_approved:
                    _rb_why = (
                        "Inversión regulatoria/mandatoria — aprobación automática."
                        if _rb_gate_reg else
                        f"Riesgo crítico cuantificado: PoF={_rb_pof_now:.1%}, "
                        f"EAL={_rb_eal_now:,.1f} MM COP."
                        if _rb_gate_crit else
                        f"Riesgo moderado: PoF={_rb_pof_now:.1%} ≥ {_rb_pof_mod:.0%} "
                        f"con EAL/OPEX sobre umbral."
                    )
                    st.markdown(
                        f'<div style="background:#052e16;border:1px solid #16a34a55;'
                        f'border-left:4px solid #22c55e;border-radius:8px;'
                        f'padding:10px 16px;font-size:0.82rem;color:#bbf7d0;">'
                        f'<b>✅ APTO para aprobación local</b> — {_rb_why}</div>',
                        unsafe_allow_html=True)
                else:
                    # Umbral no alcanzado → opciones de escalamiento o rechazo
                    _rb_why_not = (
                        f"PoF={_rb_pof_now:.1%} (umbral: {_rb_pof_mod:.0%}) · "
                        f"EAL={_rb_eal_now:,.1f} MM (umbral: {_rb_eal_thr:,.0f} MM) · "
                        f"No es mandatorio por regulación."
                    )
                    st.markdown(
                        f'<div style="background:#450a0a;border:1px solid #7f1d1d55;'
                        f'border-left:4px solid #ef4444;border-radius:8px;'
                        f'padding:10px 16px;margin-bottom:10px;font-size:0.82rem;'
                        f'color:#fca5a5;">'
                        f'<b>⚠️ No alcanza umbral de aprobación local</b><br>'
                        f'<span style="color:#94a3b8;font-size:0.78rem;">'
                        f'{_rb_why_not}</span><br>'
                        f'Seleccione cómo proceder:</div>',
                        unsafe_allow_html=True)

                    _rb_col1, _rb_col2 = st.columns(2, gap="small")
                    with _rb_col1:
                        if st.button("🔼 Escalar a análisis SIMPLIFIED",
                                     use_container_width=True,
                                     key=f"wi_rb_escalate_{_letter}",
                                     help="Sube el proyecto al carril corporativo. "
                                          "Se requerirá cuantificar beneficios con drivers VNT."):
                            st.session_state["w_model_level"] = "SIMPLIFIED"
                            st.session_state["w_capex_ref"]   = st.session_state.get("w_capex_ref", 0.0)
                            st.rerun()
                    with _rb_col2:
                        if st.button("❌ Rechazar y registrar",
                                     use_container_width=True,
                                     key=f"wi_rb_reject_{_letter}",
                                     help="Registra el proyecto como no viable. "
                                          "Queda trazado en Snapshots."):
                            _rejected_entry = {
                                "id":        f"REJ-{_w_year}-{datetime.now().strftime('%H%M%S')}",
                                "name":      st.session_state.get("w_name", "Sin nombre"),
                                "bu":        _w_bu,
                                "capex_mm":  _rb_capex_ref,
                                "pof":       _rb_pof_now,
                                "eal_mm":    _rb_eal_now,
                                "checklist": dict(_checklist_results),
                                "motivo":    "No alcanzó gate cuantitativo RULE_BASED",
                                "timestamp": datetime.now().isoformat(),
                            }
                            _rej_list = st.session_state.setdefault("w_rejected_projects", [])
                            _rej_list.append(_rejected_entry)
                            st.session_state["wizard_step"] = 1
                            st.session_state["w_name"]      = ""
                            st.success(
                                "✅ Proyecto registrado como **no viable** "
                                f"(PoF={_rb_pof_now:.1%}, EAL={_rb_eal_now:,.1f} MM). "
                                "Reiniciando wizard.")
                            st.rerun()

                _has_val = _rb_approved
                # Para RULE_BASED no se construyen drivers VNT; CAPEX como costo
                _drv_new = {}

            elif _w_model_level != "RULE_BASED" and _w_meth == "asset_aware":
                # ── ASSET-AWARE: tipo de intervención → PoF_nueva → ΔPoF ──────
                st.markdown("##### 🔵 Intervención sobre el activo")
                _aw_bl_b   = st.session_state.get("w_aw_baseline", {})
                _aw_beta   = float(_aw_bl_b.get("beta", 3.0))
                _aw_eta    = float(_aw_bl_b.get("eta",  30.0))
                _aw_age_bl = float(_aw_bl_b.get("age",  0.0))
                _aw_cof    = float(_aw_bl_b.get("cof_mm", 1.0))
                _pof_now   = float(_aw_bl_b.get("pof", 0.0))

                st.markdown(
                    f'<div style="background:#EFF6FF;border:1px solid #93C5FD;'
                    f'border-radius:8px;padding:8px 14px;margin-bottom:12px;'
                    f'font-size:0.78rem;color:#1E40AF">'
                    f'Activo actual: <b>PoF = {_pof_now:.1%}</b> · '
                    f'EAL sin intervención = <b>{_pof_now*_aw_cof:,.1f} MM COP/año</b> · '
                    f'β={_aw_beta} · η={_aw_eta}a · edad={_aw_age_bl}a</div>',
                    unsafe_allow_html=True)

                _prev_aw_b = st.session_state.get(f"w_alt_{_letter}_aw", {})
                _INT_TYPES  = {
                    "reposicion":      "🔄 Reposición total  — activo nuevo (PoF → 0)",
                    "rehabilitacion":  "🔧 Rehabilitación parcial  — vida útil extendida",
                }
                _int_type = st.radio(
                    "Tipo de intervención",
                    options=list(_INT_TYPES.keys()),
                    format_func=lambda k: _INT_TYPES[k],
                    index=0 if _prev_aw_b.get("int_type","reposicion")=="reposicion" else 1,
                    key=f"wi_aw_{_letter}_inttype",
                    horizontal=True)

                _pof_new = 0.0
                _aw_age_new = 0.0
                _max_comp_age = max(
                    (float(getattr(c, "asset_age_years", 0.0) or 0.0) for c in _base_components),
                    default=_aw_age_bl)
                if _int_type == "rehabilitacion":
                    st.caption("En rehabilitación el activo queda con una edad residual menor — "
                               "ingresa la edad efectiva post-intervención.")
                    _aw_age_new = st.number_input(
                        "Edad efectiva post-rehabilitación (años)",
                        min_value=0.0, max_value=max(_max_comp_age, 1.0),
                        value=float(_prev_aw_b.get("age_new", max(0.0, _aw_age_bl * 0.3))),
                        step=1.0, key=f"wi_aw_{_letter}_agenew",
                        help="Edad equivalente del activo después de la rehabilitación. "
                             "Reposición de aislamiento → edad ≈ 0; recubrimiento → 30% de edad original")
                    if _aw_eta > 0 and _aw_beta > 0:
                        _pof_new = _weibull_pof_at_age(_aw_beta, _aw_eta,
                                                       min(_aw_age_new, _aw_age_bl))

                # ── D-Task17/18: EAL evitada = suma sobre TODOS los componentes ──
                # `_pof_new` / `_delta_pof` quedan referidos al componente
                # principal (índice 0) para las tarjetas y la matriz C×P; el
                # beneficio en pesos que alimenta el driver VD-1 suma la
                # contribución Weibull de cada componente.
                _delta_pof  = max(_pof_now - _pof_new, 0.0)   # sólo componente 0
                _aw_eal_COP_total, _aw_eal_desglose = _asset_aware_eal_avoided_COP(
                    _base_components, _int_type, _aw_age_new)
                _aw_eal_mm  = _aw_eal_COP_total / 1_000_000.0   # beneficio: EAL evitada (todos)

                if _int_type == "rehabilitacion" and len(_aw_eal_desglose) > 1:
                    st.warning(
                        "⚠️ La rehabilitación usa **una sola edad efectiva** aplicada a "
                        f"los {len(_aw_eal_desglose)} componentes con curva Weibull "
                        "(acotada por componente a su propia edad). La reposición total "
                        "no tiene esta limitación (PoF → 0 en todos). Si los componentes "
                        "quedan con edades residuales distintas tras la rehabilitación, "
                        "regístralos como proyectos separados o verifica el desglose de "
                        "abajo antes de aprobar.")

                if len(_aw_eal_desglose) > 1:
                    _desg_rows = "".join(
                        f'<tr><td style="padding:2px 10px 2px 0">{_d["label"] or "(sin nombre)"}</td>'
                        f'<td style="padding:2px 10px;text-align:right">{_d["pof_now"]:.1%}</td>'
                        f'<td style="padding:2px 10px;text-align:right">{_d["pof_new"]:.1%}</td>'
                        f'<td style="padding:2px 10px;text-align:right">{_d["cof_COP"]/1e6:,.0f}</td>'
                        f'<td style="padding:2px 0 2px 10px;text-align:right;font-weight:700">'
                        f'{_d["eal_avoided_COP"]/1e6:,.1f}</td></tr>'
                        for _d in _aw_eal_desglose)
                    st.markdown(
                        f'<div style="font-size:0.72rem;color:{EPM_GRAY};margin:4px 0">'
                        f'<b>EAL evitada por componente (MM COP/año)</b>'
                        f'<table style="border-collapse:collapse;margin-top:3px">'
                        f'<tr style="color:{EPM_GRAY};font-size:0.66rem">'
                        f'<td style="padding-right:10px">Componente</td>'
                        f'<td style="padding:0 10px;text-align:right">PoF hoy</td>'
                        f'<td style="padding:0 10px;text-align:right">PoF post</td>'
                        f'<td style="padding:0 10px;text-align:right">CoF</td>'
                        f'<td style="padding-left:10px;text-align:right">EAL evit.</td></tr>'
                        f'{_desg_rows}'
                        f'<tr style="border-top:1px solid {EPM_GRAY}55;font-weight:800">'
                        f'<td colspan="4" style="padding-top:3px">Total</td>'
                        f'<td style="padding-top:3px;text-align:right">{_aw_eal_mm:,.1f}</td></tr>'
                        f'</table></div>',
                        unsafe_allow_html=True)

                # Visualización ΔPoF
                _aw_va, _aw_vb, _aw_vc = st.columns(3, gap="medium")
                _pof_color = EPM_RED if _pof_now > 0.5 else (EPM_AMBER if _pof_now > 0.2 else EPM_DARK)
                _n_comp_curva = len(_aw_eal_desglose)
                _pof_card_lbl = ("PoF sin intervención" if _n_comp_curva <= 1
                                 else "PoF sin interv. (comp. 1)")
                _pof_new_lbl  = ("PoF con intervención" if _n_comp_curva <= 1
                                 else "PoF con interv. (comp. 1)")
                _eal_card_lbl = ("EAL evitada (beneficio)" if _n_comp_curva <= 1
                                 else f"EAL evitada — total {_n_comp_curva} comp.")
                with _aw_va:
                    st.markdown(
                        f'<div style="background:{_pof_color}15;border:1.5px solid {_pof_color};'
                        f'border-radius:8px;padding:10px;text-align:center">'
                        f'<div style="font-size:0.65rem;color:{EPM_GRAY}">{_pof_card_lbl}</div>'
                        f'<div style="font-size:1.3rem;font-weight:800;color:{_pof_color}">'
                        f'{_pof_now:.1%}</div></div>', unsafe_allow_html=True)
                with _aw_vb:
                    st.markdown(
                        f'<div style="background:{EPM_DARK}15;border:1.5px solid {EPM_DARK};'
                        f'border-radius:8px;padding:10px;text-align:center">'
                        f'<div style="font-size:0.65rem;color:{EPM_GRAY}">{_pof_new_lbl}</div>'
                        f'<div style="font-size:1.3rem;font-weight:800;color:{EPM_DARK}">'
                        f'{_pof_new:.1%}</div></div>', unsafe_allow_html=True)
                with _aw_vc:
                    st.markdown(
                        f'<div style="background:{EPM_GREEN}22;border:2px solid {EPM_GREEN};'
                        f'border-radius:8px;padding:10px;text-align:center">'
                        f'<div style="font-size:0.65rem;color:{EPM_GRAY}">{_eal_card_lbl}</div>'
                        f'<div style="font-size:1.3rem;font-weight:800;color:{EPM_DARK}">'
                        f'{_aw_eal_mm:,.1f} MM/año</div></div>', unsafe_allow_html=True)

                # Construir drivers desde ΔPoF × CoF
                _aw_sc_id = None
                _af_key_b2 = st.session_state.get("w_asset_family", "otro")
                for _sc_id_aw, _sc_info_aw in _sc_active.items():
                    if (_sc_info_aw.get("asset_family") == _af_key_b2
                            and _sc_info_aw.get("metodologia_sugerida") == "asset_aware"):
                        _aw_sc_id = _sc_id_aw
                        break
                if _aw_sc_id is None and _sc_active:
                    _aw_sc_id = next(iter(_sc_active))

                if _aw_sc_id and _aw_eal_mm > 0:
                    _aw_sc_price = float(_sc_active.get(_aw_sc_id, {}).get("price_COP", 1) or 1)
                    _drv_new[_aw_sc_id] = (_aw_eal_mm * 1e6) / _aw_sc_price if _aw_sc_price else 0.0
                    _has_val = True
                else:
                    _has_val = _aw_eal_mm > 0

                # Niveles de riesgo residual para RiskInput (derivados de Weibull)
                _d_rrc = _cof_to_level(_aw_cof)     # consecuencia no cambia
                _d_rrp = _pof_to_level(_pof_new)    # probabilidad residual post-intervención

                # ── Matriz de Riesgo C55 — Baseline → Residual (asset_aware) ─
                # Muestra todos los tipos: financiero de Weibull + tipos adicionales
                # (H&S, Ambiental, etc.) que el analista ingresó en Bloque 2.
                st.markdown("##### 🗺️ Movimiento de Riesgo — Do Nothing → Alt. " + _letter)
                st.caption(
                    "Círculo = posición baseline por tipo. "
                    "Flecha + marcador = posición residual. "
                    f"Financiero: C{_b_rbc_v}×P{_b_rbp_v} → C{_d_rrc}×P{_d_rrp} "
                    f"({_b_rbc_v*_b_rbp_v - _d_rrc*_d_rrp:+d} pts).")

                # Construir snapshots baseline completos (financiero + adicionales,
                # unión de todos los componentes guardados en FASE A — D-Task18)
                _bl_ctypes_full_aw    = dict(_bl_ctypes_aw)
                _bl_ctypes_probs_full_aw = dict(_bl_ctypes_probs_aw)
                _bl_ctypes_full_aw.setdefault("financiero", _b_rbc_v)
                _bl_ctypes_probs_full_aw.setdefault("financiero", _b_rbp_v)

                # Residual financiero de Weibull; adicionales desde Bloque 2
                _res_types_full_aw  = dict(locals().get("_d_residual_consequence_types",
                                                        {"financiero": _d_rrc}))
                _res_probs_full_aw  = dict(locals().get("_d_residual_consequence_type_probs",
                                                        {"financiero": _d_rrp}))
                _res_types_full_aw["financiero"]  = _d_rrc   # asegurar valor Weibull real
                _res_probs_full_aw["financiero"]  = _d_rrp

                _ctm_cfg_aw_b3 = epm_config.get("risk_matrix", {}).get("consequence_types_matrix", {})
                _fig_rm_aw_alt = _build_risk_matrix_fig(
                    bl_types=_bl_ctypes_full_aw,
                    bl_probs=_bl_ctypes_probs_full_aw,
                    ctm_config=_ctm_cfg_aw_b3,
                    title=f"Alt. {_letter} — Riesgo: Do Nothing → Residual ({len(_res_types_full_aw)} tipo{'s' if len(_res_types_full_aw)>1 else ''})",
                    alt_data=[{
                        "label": f"Alt {_letter}",
                        "color": EPM_DARK,
                        "res_types": _res_types_full_aw,
                        "res_probs": _res_probs_full_aw,
                    }],
                )
                st.plotly_chart(_fig_rm_aw_alt, use_container_width=True,
                                config={"displayModeBar": False})

                # ── VDs adicionales (Copperleaf: aditivo por Value Dimension) ──
                # VD-1 ya capturado por Weibull (EAL evitada). Los demás VDs
                # seleccionados en Step 2 se valoran con Driver-Based independiente.
                _aw_other_scs = {
                    k: v for k, v in _sc_active.items()
                    if k != _aw_sc_id
                    and v.get("metodologia_sugerida") != "asset_aware"
                }
                if _aw_other_scs:
                    st.markdown("---")
                    st.markdown("##### 💰 Beneficios adicionales en otros Value Dimensions")
                    st.markdown(
                        f'<div style="background:#F0FDF4;border:1px solid #B3EFD6;'
                        f'border-radius:8px;padding:8px 14px;margin-bottom:10px;'
                        f'font-size:0.76rem;color:#065F46">'
                        f'Copperleaf trata cada VD de forma independiente. VD-1 ya quedó '
                        f'valorado por Weibull. Declara aquí el impacto en los demás retos '
                        f'usando el mejor dato disponible (Driver-Based o Concept Rating).</div>',
                        unsafe_allow_html=True)
                    _aw_vd_groups: dict = {}
                    for _sc_id, _sc_info in _aw_other_scs.items():
                        _aw_vd_groups.setdefault(_sc_info.get("vd_id","?"), {})[_sc_id] = _sc_info
                    for _vd_id_aw, _scs_aw in _aw_vd_groups.items():
                        _vd_label_aw = {
                            "VD-2": "🟢 VD-2 — Servicios Eficientes",
                            "VD-3": "🟡 VD-3 — Cobertura Universal Sostenible",
                            "VD-4": "🌿 VD-4 — Protección Hídrica y Carbono Neutralidad",
                            "VD-5": "💰 VD-5 — Generación de Valor",
                            "VD-6": "📣 VD-6 — Reputación",
                        }.get(_vd_id_aw, f"📊 {_vd_id_aw}")
                        with st.expander(_vd_label_aw, expanded=False):
                            for _sc_id, _sc_info in _scs_aw.items():
                                _prev_v_aw = float(_prev_drv.get(_sc_id, 0.0))
                                _unit_aw   = _sc_info.get("unit", "")
                                _price_aw  = float(_sc_info.get("price_COP", 0) or 0)
                                _plbl_aw   = (f"Precio: {_price_aw/1e6:.4f} MM COP/{_unit_aw}"
                                              if _price_aw > 0 else "Sin precio regulatorio")
                                _col_a_aw, _col_b_aw = st.columns([5, 1], gap="small")
                                with _col_a_aw:
                                    st.markdown(
                                        f'<div style="font-size:0.84rem;color:{EPM_SLATE};'
                                        f'font-weight:600;padding-top:6px">'
                                        f'{_sc_id} · {_sc_info.get("label","")}</div>'
                                        f'<div style="font-size:0.70rem;color:{EPM_GRAY}">'
                                        f'{_plbl_aw}</div>',
                                        unsafe_allow_html=True)
                                with _col_b_aw:
                                    _val_aw = st.number_input(
                                        f"Δ {_unit_aw}",
                                        min_value=0.0,
                                        value=_prev_v_aw,
                                        key=f"wi_aw_add_{_letter}_{_sc_id}",
                                        label_visibility="collapsed")
                                _drv_new[_sc_id] = _val_aw
                                if _val_aw > 0:
                                    _has_val = True
                                    _ben_yr_aw = _val_aw * _price_aw / 1e6
                                    st.markdown(
                                        f'<div style="font-size:0.72rem;color:{EPM_DARK};'
                                        f'margin-top:-4px">'
                                        f'↳ Beneficio anual: <b>{_ben_yr_aw:,.2f} MM COP/año</b>'
                                        f'</div>',
                                        unsafe_allow_html=True)

            else:
                # ── SIMPLIFIED / FULL: entrada de drivers cuantitativa ─────────
                st.markdown("##### 💰 Beneficios esperados")
                if _w_meth == "concept_rating":
                    st.markdown(
                        f'<div style="background:#FFFBEB;border:1px solid #FDE68A;'
                        f'border-radius:8px;padding:8px 14px;margin-bottom:10px;'
                        f'font-size:0.78rem;color:#92400E">'
                        f'<b>Escala 0-10 (C55):</b> 0 = Sin impacto · 5 = Igual que Do Nothing · '
                        f'10 = Transformacional. Solo valores &gt; 5 generan beneficio marginal.</div>',
                        unsafe_allow_html=True)
                    _vd_groups_cr: dict = {}
                    for _sc_id, _sc_info in _sc_active.items():
                        _vd_groups_cr.setdefault(_sc_info.get("vd_id", "?"), {})[_sc_id] = _sc_info
                    for _vd_id_cr, _scs_cr in _vd_groups_cr.items():
                        with st.expander(f"📊 {_vd_id_cr}", expanded=True):
                            for _sc_id, _sc_info in _scs_cr.items():
                                _prev_cr = int(_prev_drv.get(_sc_id, 5))
                                _sc_val  = st.slider(
                                    f'{_sc_info.get("label", _sc_id)} [{_sc_info.get("unit","")}]',
                                    0, 10, _prev_cr,
                                    key=f"wi_cr_{_letter}_{_sc_id}")
                                _drv_new[_sc_id] = float(_sc_val)
                                if _sc_val > 5:
                                    _has_val = True
                else:
                    # Driver-Based / Asset-Aware: Δ cuantitativo
                    _vd_groups_db: dict = {}
                    for _sc_id, _sc_info in _sc_active.items():
                        _reto_db = _sc_info.get("reto", "Otros")
                        _vd_groups_db.setdefault(_reto_db, {})[_sc_id] = _sc_info
                    # Leer datos del gap (calculados en FASE A)
                    _gap_ok       = st.session_state.get("w_gap_computed", False)
                    _gap_ens_ann  = st.session_state.get("w_ens_annual_mm",  0.0)
                    _gap_ens_tot  = st.session_state.get("w_ens_mm",         0.0)
                    _gap_yr_ss    = st.session_state.get("w_gap_year",       None)
                    _gap_unit_ss  = st.session_state.get("w_gap_units",      "unidades")

                    for _reto_lbl, _scs in _vd_groups_db.items():
                        # ¿Este grupo pertenece a VD-3 (Cobertura)?
                        _is_vd3_group = any(
                            _s.get("vd_id") == "VD-3" for _s in _scs.values())

                        with st.expander(f"📊 {_reto_lbl}", expanded=True):
                            # ── Banner ENS sugerida (solo VD-3 + gap calculado) ──
                            if _is_vd3_group and _gap_ok and _gap_ens_ann > 0:
                                st.markdown(
                                    f'<div style="background:#EFF6FF;border:1.5px solid {EPM_BLUE};'
                                    f'border-radius:8px;padding:8px 12px;margin-bottom:8px;'
                                    f'font-size:0.76rem;color:{EPM_BLUE}">'
                                    f'<b>💡 Brecha de capacidad calculada en FASE A:</b> '
                                    f'ENS anual promedio = <b>{_gap_ens_ann:,.1f} MM COP/año</b> '
                                    f'(año de cruce: {_gap_yr_ss}, unidad: {_gap_unit_ss}). '
                                    f'Usa el botón <b>↙ Aplicar ENS</b> para pre-llenar el driver.</div>',
                                    unsafe_allow_html=True)

                            for _sc_id, _sc_info in _scs.items():
                                _prev_v  = float(_prev_drv.get(_sc_id, 0.0))
                                _unit_v  = _sc_info.get("unit", "")
                                _price_v = float(_sc_info.get("price_COP", 0) or 0)
                                _plbl    = (f"Precio regulatorio: {_price_v/1e6:.4f} MM COP/{_unit_v}"
                                            if _price_v > 0 else "Sin precio regulatorio definido")

                                # ── Sugerencia ENS para este sub-criterio VD-3 ──
                                _is_vd3_sc = (_sc_info.get("vd_id") == "VD-3")
                                if _is_vd3_sc and _gap_ok and _gap_ens_ann > 0 and _price_v > 0:
                                    _sug_delta = (_gap_ens_ann * 1e6) / _price_v
                                    _sug_key   = f"wi_gap_apply_{_letter}_{_sc_id}"
                                    if st.button(
                                        f"↙ Aplicar ENS sugerida ({_sug_delta:,.0f} {_unit_v}/año)",
                                        key=_sug_key,
                                        help=(f"Pre-llena este driver con el valor derivado de la "
                                              f"Brecha de Capacidad: {_gap_ens_ann:,.1f} MM COP/año "
                                              f"÷ {_price_v/1e6:.4f} MM COP/{_unit_v} "
                                              f"= {_sug_delta:,.0f} {_unit_v}/año")):
                                        st.session_state[f"wi_db_{_letter}_{_sc_id}"] = _sug_delta
                                        st.rerun()

                                # ── Calculadora CREG 015/2018 (VD-5-D · TyD) ────────
                                if _sc_id == "VD-5-D":
                                    _creg_params = _sc_info.get("_creg_params", {})
                                    _creg_wacc   = _creg_params.get("wacc_reg", 0.113)
                                    _creg_aclass = _creg_params.get("asset_classes", {})
                                    with st.expander(
                                        "🏛️ Calculadora CREG 015/2018 — Remuneración SDL/STN",
                                        expanded=(_prev_v == 0.0)):
                                        st.caption(
                                            "Remuneración anual = VRN × (WACC_reg + Tasa_Dep) "
                                            "— CREG Res.015/2018 Art.15 (SDL) / Res.011/2009 (STN).")
                                        _cc1, _cc2 = st.columns([1, 1], gap="medium")
                                        with _cc1:
                                            _creg_vrn = st.number_input(
                                                "Valor de Reposición a Nuevo — VRN (MM COP)",
                                                min_value=0.0,
                                                value=float(st.session_state.get(
                                                    f"wi_creg_vrn_{_letter}", 0.0)),
                                                step=100.0,
                                                key=f"wi_creg_vrn_{_letter}_inp",
                                                help=(
                                                    "VRN del activo nuevo calculado con precios "
                                                    "CREG vigentes. Fuente: SUI / UPME o cálculo "
                                                    "propio con precios unitarios Res.015/2018 Anexo."))
                                            st.session_state[f"wi_creg_vrn_{_letter}"] = _creg_vrn
                                        with _cc2:
                                            _creg_cls_lbl = st.selectbox(
                                                "Clase de activo (tasa depreciación CREG)",
                                                options=list(_creg_aclass.keys()),
                                                index=0,
                                                key=f"wi_creg_cls_{_letter}_inp",
                                                help="Define la tasa de depreciación regulatoria anual.")
                                            _creg_dep = _creg_aclass.get(_creg_cls_lbl, 0.025)
                                        _creg_rate  = _creg_wacc + _creg_dep
                                        _creg_ann   = _creg_vrn * _creg_rate  # MM COP/año
                                        _creg_c1, _creg_c2, _creg_c3 = st.columns(3, gap="small")
                                        with _creg_c1:
                                            st.markdown(
                                                f'<div style="background:#EFF6FF;border:1px solid #93C5FD;'
                                                f'border-radius:6px;padding:8px;text-align:center">'
                                                f'<div style="font-size:0.60rem;color:#94A3B8">WACC_CREG</div>'
                                                f'<b style="color:#1E40AF;font-size:0.95rem">'
                                                f'{_creg_wacc:.1%}</b></div>',
                                                unsafe_allow_html=True)
                                        with _creg_c2:
                                            st.markdown(
                                                f'<div style="background:#EFF6FF;border:1px solid #93C5FD;'
                                                f'border-radius:6px;padding:8px;text-align:center">'
                                                f'<div style="font-size:0.60rem;color:#94A3B8">Tasa Dep.</div>'
                                                f'<b style="color:#1E40AF;font-size:0.95rem">'
                                                f'{_creg_dep:.1%}</b></div>',
                                                unsafe_allow_html=True)
                                        with _creg_c3:
                                            st.markdown(
                                                f'<div style="background:#F0FDF4;border:1.5px solid #22C55E;'
                                                f'border-radius:6px;padding:8px;text-align:center">'
                                                f'<div style="font-size:0.60rem;color:#94A3B8">'
                                                f'Remuneración anual</div>'
                                                f'<b style="color:{EPM_DARK};font-size:0.95rem">'
                                                f'{_creg_ann:,.2f} MM COP/año</b></div>',
                                                unsafe_allow_html=True)
                                        st.caption(
                                            f"VRN {_creg_vrn:,.0f} × (WACC_reg {_creg_wacc:.1%} + "
                                            f"Dep. {_creg_dep:.1%}) = {_creg_ann:,.2f} MM COP/año")
                                        if _creg_ann > 0:
                                            if st.button(
                                                f"↙ Aplicar remuneración CREG ({_creg_ann:,.2f} MM COP/año)",
                                                key=f"wi_creg_apply_{_letter}_{_sc_id}",
                                                help=(
                                                    "Pre-llena el campo de ingreso anual regulado con "
                                                    "el valor calculado. Puedes ajustarlo manualmente.")):
                                                st.session_state[f"wi_db_{_letter}_{_sc_id}"] = _creg_ann
                                                st.rerun()
                                        st.caption(
                                            "⚠️ La remuneración CREG se reconoce desde la fecha de "
                                            "entrada en operación comercial del activo. "
                                            "El motor aplica el FD_lag de Copperleaf C55 §5.3 "
                                            "para ajustar por años de construcción automáticamente.")

                                # ── Calculadora CREG Gas (VD-5-E · Gas) ─────────────
                                if _sc_id == "VD-5-E" and _w_bu == "Gas":
                                    _gp = _sc_info.get("_creg_params", {})
                                    _gp_wacc  = _gp.get("wacc_reg", 0.1094)
                                    _gp_nota  = _gp.get("wacc_nota", "")
                                    _gp_cls   = _gp.get("asset_classes", {})
                                    with st.expander(
                                        "🔥 Calculadora CREG Gas — Remuneración red (Cargo D)",
                                        expanded=(_prev_v == 0.0)):
                                        st.warning(
                                            "⚠️ **Riesgo regulatorio WACC Gas:** " + _gp_nota,
                                            icon="⚠️")
                                        st.caption(
                                            "Remuneración anual = VRN × (WACC_gas + Tasa_Dep) "
                                            "— CREG Res.126/2010 · Res.4/2021 · Res.125/2020.")
                                        _gc1, _gc2 = st.columns(2, gap="medium")
                                        with _gc1:
                                            _g_vrn = st.number_input(
                                                "Valor de Reposición a Nuevo — VRN (MM COP)",
                                                min_value=0.0,
                                                value=float(st.session_state.get(
                                                    f"wi_gvrn_{_letter}", 0.0)),
                                                step=50.0,
                                                key=f"wi_gvrn_{_letter}_inp",
                                                help=(
                                                    "VRN del activo nuevo. "
                                                    "Fuente: CREG/SUI o ingeniería de detalle."))
                                            st.session_state[f"wi_gvrn_{_letter}"] = _g_vrn
                                        with _gc2:
                                            _g_cls = st.selectbox(
                                                "Clase de activo (tasa dep. CREG Gas)",
                                                options=list(_gp_cls.keys()),
                                                index=0,
                                                key=f"wi_gcls_{_letter}_inp",
                                                help="Define la tasa de depreciación regulatoria.")
                                            _g_dep = _gp_cls.get(_g_cls, 0.025)
                                        _g_wacc_ed = st.number_input(
                                            "WACC regulatorio gas (% editable)",
                                            min_value=5.0, max_value=20.0,
                                            value=float(_gp_wacc * 100),
                                            step=0.01,
                                            key=f"wi_gwacc_{_letter}_inp",
                                            help="Default: 10.94% (CREG Res.4/2021). Editable ante cambios futuros.",
                                            format="%.2f") / 100.0
                                        _g_rate = _g_wacc_ed + _g_dep
                                        _g_ann  = _g_vrn * _g_rate
                                        _gc3, _gc4, _gc5 = st.columns(3, gap="small")
                                        with _gc3:
                                            st.markdown(
                                                f'<div style="background:#FFF7ED;border:1px solid #F97316;'
                                                f'border-radius:6px;padding:8px;text-align:center">'
                                                f'<div style="font-size:0.60rem;color:#94A3B8">WACC_gas (editable)</div>'
                                                f'<b style="color:#C2410C;font-size:0.95rem">'
                                                f'{_g_wacc_ed:.2%}</b></div>',
                                                unsafe_allow_html=True)
                                        with _gc4:
                                            st.markdown(
                                                f'<div style="background:#FFF7ED;border:1px solid #F97316;'
                                                f'border-radius:6px;padding:8px;text-align:center">'
                                                f'<div style="font-size:0.60rem;color:#94A3B8">Tasa Dep.</div>'
                                                f'<b style="color:#C2410C;font-size:0.95rem">'
                                                f'{_g_dep:.1%}</b></div>',
                                                unsafe_allow_html=True)
                                        with _gc5:
                                            st.markdown(
                                                f'<div style="background:#F0FDF4;border:1.5px solid #22C55E;'
                                                f'border-radius:6px;padding:8px;text-align:center">'
                                                f'<div style="font-size:0.60rem;color:#94A3B8">Remuneración anual</div>'
                                                f'<b style="color:{EPM_DARK};font-size:0.95rem">'
                                                f'{_g_ann:,.2f} MM COP/año</b></div>',
                                                unsafe_allow_html=True)
                                        st.caption(
                                            f"VRN {_g_vrn:,.0f} × (WACC {_g_wacc_ed:.2%} + "
                                            f"Dep. {_g_dep:.1%}) = {_g_ann:,.2f} MM COP/año")
                                        if _g_ann > 0:
                                            if st.button(
                                                f"↙ Aplicar remuneración gas ({_g_ann:,.2f} MM COP/año)",
                                                key=f"wi_gapply_{_letter}_{_sc_id}"):
                                                st.session_state[f"wi_db_{_letter}_{_sc_id}"] = _g_ann
                                                st.rerun()
                                        st.caption(
                                            "ℹ️ Reconocida desde entrada en operación comercial. "
                                            "FD_lag C55 §5.3 ajusta por años de construcción.")

                                # ── Calculadora CRA AyA (VD-5-E · AyA) ──────────────
                                if _sc_id == "VD-5-E" and _w_bu == "AyA":
                                    _ap = _sc_info.get("_cra_params", {})
                                    _ap_wacc  = _ap.get("wacc_cra", 0.101)
                                    _ap_nota  = _ap.get("wacc_nota", "")
                                    _ap_cls   = _ap.get("asset_classes", {})
                                    _ap_mkey  = f"wi_aya_meth_{_letter}"
                                    _ap_meth  = st.session_state.get(_ap_mkey, "aproximacion")
                                    with st.expander(
                                        "💧 Calculadora CRA — CMI Infraestructura (Res.688/2014)",
                                        expanded=(_prev_v == 0.0)):
                                        st.caption(_sc_info.get("_nota", ""))
                                        _ap_meth = st.radio(
                                            "Método de cálculo del ingreso regulado CRA",
                                            options=["aproximacion", "directo"],
                                            format_func=lambda x: (
                                                "Aproximación: ΔVPI × (WACC_CRA + Dep.)"
                                                if x == "aproximacion" else
                                                "Ingreso directo (MM COP/año calculado externamente)"),
                                            index=0 if _ap_meth == "aproximacion" else 1,
                                            key=f"wi_aya_meth_{_letter}_inp",
                                            horizontal=True)
                                        st.session_state[_ap_mkey] = _ap_meth

                                        if _ap_meth == "aproximacion":
                                            st.info(
                                                "**Aproximación:** modela el retorno como VPI × tasa, "
                                                "igual que T&D eléctrico. El CMI real es volumétrico "
                                                "($/m³); esta simplificación es válida como referencia. "
                                                "La inversión debe estar dentro del Plan CRA aprobado.",
                                                icon="ℹ️")
                                            _am1, _am2 = st.columns(2, gap="medium")
                                            with _am1:
                                                _a_vpi = st.number_input(
                                                    "ΔVPI — Valor del plan de inversiones reconocido (MM COP)",
                                                    min_value=0.0,
                                                    value=float(st.session_state.get(
                                                        f"wi_avpi_{_letter}", 0.0)),
                                                    step=50.0,
                                                    key=f"wi_avpi_{_letter}_inp",
                                                    help=(
                                                        "Valor presente del incremento al plan de inversiones "
                                                        "aprobado por CRA para este activo. "
                                                        "Fuente: estudio tarifario / herramienta EAFI CRA."))
                                                st.session_state[f"wi_avpi_{_letter}"] = _a_vpi
                                            with _am2:
                                                _a_cls = st.selectbox(
                                                    "Clase de activo (tasa dep. CRA)",
                                                    options=list(_ap_cls.keys()),
                                                    index=0,
                                                    key=f"wi_acls_{_letter}_inp")
                                                _a_dep = _ap_cls.get(_a_cls, 0.025)
                                            _a_wacc_ed = st.number_input(
                                                "WACC_CRA (% editable)",
                                                min_value=5.0, max_value=20.0,
                                                value=float(_ap_wacc * 100),
                                                step=0.01,
                                                key=f"wi_awacc_{_letter}_inp",
                                                help=_ap_nota,
                                                format="%.2f") / 100.0
                                            _a_ann = _a_vpi * (_a_wacc_ed + _a_dep)
                                            _ac1, _ac2, _ac3 = st.columns(3, gap="small")
                                            with _ac1:
                                                st.markdown(
                                                    f'<div style="background:#F0FDFA;border:1px solid #5EEAD4;'
                                                    f'border-radius:6px;padding:8px;text-align:center">'
                                                    f'<div style="font-size:0.60rem;color:#94A3B8">WACC_CRA</div>'
                                                    f'<b style="color:#0F766E;font-size:0.95rem">'
                                                    f'{_a_wacc_ed:.2%}</b></div>',
                                                    unsafe_allow_html=True)
                                            with _ac2:
                                                st.markdown(
                                                    f'<div style="background:#F0FDFA;border:1px solid #5EEAD4;'
                                                    f'border-radius:6px;padding:8px;text-align:center">'
                                                    f'<div style="font-size:0.60rem;color:#94A3B8">Tasa Dep.</div>'
                                                    f'<b style="color:#0F766E;font-size:0.95rem">'
                                                    f'{_a_dep:.1%}</b></div>',
                                                    unsafe_allow_html=True)
                                            with _ac3:
                                                st.markdown(
                                                    f'<div style="background:#F0FDF4;border:1.5px solid #22C55E;'
                                                    f'border-radius:6px;padding:8px;text-align:center">'
                                                    f'<div style="font-size:0.60rem;color:#94A3B8">CMI aprox.</div>'
                                                    f'<b style="color:{EPM_DARK};font-size:0.95rem">'
                                                    f'{_a_ann:,.2f} MM COP/año</b></div>',
                                                    unsafe_allow_html=True)
                                            st.caption(
                                                f"ΔVPI {_a_vpi:,.0f} × (WACC {_a_wacc_ed:.2%} + "
                                                f"Dep. {_a_dep:.1%}) = {_a_ann:,.2f} MM COP/año")
                                            if _a_ann > 0:
                                                if st.button(
                                                    f"↙ Aplicar CMI aprox. ({_a_ann:,.2f} MM COP/año)",
                                                    key=f"wi_aapply_{_letter}_{_sc_id}"):
                                                    st.session_state[f"wi_db_{_letter}_{_sc_id}"] = _a_ann
                                                    st.rerun()
                                        else:
                                            # Opción B — ingreso directo
                                            st.info(
                                                "**Ingreso directo:** el analista calcula el ΔCMI × m³/año "
                                                "con la herramienta oficial CRA (EAFI) y registra el resultado. "
                                                "La inversión debe estar dentro del Plan de Inversiones CRA aprobado.",
                                                icon="ℹ️")
                                            _a_dir = st.number_input(
                                                "Ingreso regulado CRA directo (MM COP/año)",
                                                min_value=0.0,
                                                value=float(st.session_state.get(
                                                    f"wi_adir_{_letter}", 0.0)),
                                                step=10.0,
                                                key=f"wi_adir_{_letter}_inp",
                                                help=(
                                                    "Resultado de: ΔCMI ($/m³) × Volumen_anual (m³/año) "
                                                    "convertido a MM COP/año. Fuente: herramienta EAFI CRA "
                                                    "o estudio tarifario CRA 906/2019."))
                                            st.session_state[f"wi_adir_{_letter}"] = _a_dir
                                            if _a_dir > 0:
                                                if st.button(
                                                    f"↙ Aplicar ingreso CRA ({_a_dir:,.2f} MM COP/año)",
                                                    key=f"wi_adirect_apply_{_letter}_{_sc_id}"):
                                                    st.session_state[f"wi_db_{_letter}_{_sc_id}"] = _a_dir
                                                    st.rerun()
                                        st.caption(
                                            "⚠️ Restricción CRA: solo se reconoce en tarifa la "
                                            "inversión incluida en el Plan de Inversiones aprobado "
                                            "para el período tarifario vigente (CRA 906/2019).")

                                # ── Calculadora ENFICC Generación (VD-5-A · Generacion) ─
                                if _sc_id == "VD-5-A" and _w_bu == "Generacion":
                                    _enf_key = f"wi_enf_tipo_{_letter}"
                                    _enf_tipo = st.session_state.get(_enf_key, "ENFICC")
                                    with st.expander(
                                        "⚡ Tipo de ingreso de generación — Calculadora ENFICC",
                                        expanded=False):
                                        _enf_tipo = st.selectbox(
                                            "Tipo de ingreso de generación",
                                            options=["ENFICC (Cargo de Confiabilidad)",
                                                     "PPA / Contrato bilateral",
                                                     "Ingresos bolsa (spot)",
                                                     "Otro / combinado"],
                                            index=0,
                                            key=f"wi_enf_tipo_{_letter}_inp",
                                            help=(
                                                "ENFICC: ingresos por OEF asignada en subasta D+4 (CREG 071/2006). "
                                                "PPA: precio de contrato negociado. "
                                                "Spot: despacho en bolsa XM (alta volatilidad)."))
                                        st.session_state[_enf_key] = _enf_tipo

                                        if _enf_tipo.startswith("ENFICC"):
                                            st.markdown(
                                                "**Calculadora ENFICC** — `ΔOEF × Cargo = Ingreso anual`")
                                            st.caption(
                                                "Aplica cuando la inversión restituye o incrementa la "
                                                "Energía Firme (OEF) asignada en la subasta de Cargo de "
                                                "Confiabilidad (CREG Res.071/2006).")
                                            _ef1, _ef2 = st.columns(2, gap="medium")
                                            with _ef1:
                                                _e_oef = st.number_input(
                                                    "ΔOEF — Incremento energía firme (GWh/año)",
                                                    min_value=0.0,
                                                    value=float(st.session_state.get(
                                                        f"wi_eoef_{_letter}", 0.0)),
                                                    step=1.0,
                                                    key=f"wi_eoef_{_letter}_inp",
                                                    help=(
                                                        "Incremento en la Obligación de Energía Firme "
                                                        "que habilita la inversión. Fuente: XM / estudio "
                                                        "de confiabilidad de la planta."))
                                                st.session_state[f"wi_eoef_{_letter}"] = _e_oef
                                            with _ef2:
                                                _e_cargo_tipo = st.radio(
                                                    "Tipo de planta (precio Cargo)",
                                                    options=["Renovable (OEF 3, 2024)",
                                                             "Hidro convencional (precio escasez)"],
                                                    index=0,
                                                    key=f"wi_ecargo_tipo_{_letter}_inp",
                                                    help=(
                                                        "Renovable: ~73 COP/kWh (subasta OEF 3, feb.2024, "
                                                        "USD 18.2/MWh). "
                                                        "Hidro: ~30 COP/kWh (precio escasez histórico)."),
                                                    horizontal=False)
                                                _e_cargo_def = (73.0 if "Renovable" in _e_cargo_tipo
                                                                else 30.0)
                                                _e_cargo = st.number_input(
                                                    "Cargo ENFICC vigente (COP/kWh, editable)",
                                                    min_value=1.0, max_value=500.0,
                                                    value=float(st.session_state.get(
                                                        f"wi_ecargo_{_letter}", _e_cargo_def)),
                                                    step=1.0,
                                                    key=f"wi_ecargo_{_letter}_inp",
                                                    help=(
                                                        "Precio de la subasta vigente. "
                                                        "OEF 3 (2024): 73 COP/kWh (renovables). "
                                                        "Precio de escasez: ~30 COP/kWh (hidro). "
                                                        "El Cargo se redefine en cada subasta D+4."))
                                                st.session_state[f"wi_ecargo_{_letter}"] = _e_cargo
                                            # ΔOEF (GWh/año) × 1e6 kWh/GWh × Cargo (COP/kWh)
                                            # → COP/año → MM COP/año
                                            _e_ann = _e_oef * 1_000_000 * _e_cargo / 1_000_000
                                            _ee1, _ee2 = st.columns(2, gap="small")
                                            with _ee1:
                                                st.markdown(
                                                    f'<div style="background:#FEF3C7;border:1px solid #F59E0B;'
                                                    f'border-radius:6px;padding:8px;text-align:center">'
                                                    f'<div style="font-size:0.60rem;color:#94A3B8">'
                                                    f'ΔOEF × Cargo</div>'
                                                    f'<b style="color:#92400E;font-size:0.9rem">'
                                                    f'{_e_oef:,.1f} GWh × {_e_cargo:.0f} COP/kWh</b></div>',
                                                    unsafe_allow_html=True)
                                            with _ee2:
                                                st.markdown(
                                                    f'<div style="background:#F0FDF4;border:1.5px solid #22C55E;'
                                                    f'border-radius:6px;padding:8px;text-align:center">'
                                                    f'<div style="font-size:0.60rem;color:#94A3B8">'
                                                    f'Ingreso ENFICC anual</div>'
                                                    f'<b style="color:{EPM_DARK};font-size:0.95rem">'
                                                    f'{_e_ann:,.2f} MM COP/año</b></div>',
                                                    unsafe_allow_html=True)
                                            if _e_ann > 0:
                                                if st.button(
                                                    f"↙ Aplicar ingreso ENFICC ({_e_ann:,.2f} MM COP/año)",
                                                    key=f"wi_eapply_{_letter}_{_sc_id}"):
                                                    st.session_state[f"wi_db_{_letter}_{_sc_id}"] = _e_ann
                                                    st.rerun()
                                            st.warning(
                                                "⚠️ El Cargo de Confiabilidad se redefine en cada "
                                                "subasta D+4 (OEF 4 planificada 2025-2026). "
                                                "El precio de 73 COP/kWh corresponde a OEF 3 (feb.2024) "
                                                "y puede cambiar significativamente.",
                                                icon="⚠️")
                                        else:
                                            # PPA / Spot / Otro — solo nota explicativa
                                            _tipo_nota = {
                                                "PPA / Contrato bilateral": (
                                                    "Ingresa el ingreso anual incremental del contrato "
                                                    "PPA en MM COP/año. Precio negociado libremente; "
                                                    "no usa calculadora regulatoria."),
                                                "Ingresos bolsa (spot)": (
                                                    "⚠️ Ingreso spot (bolsa XM) es altamente volátil. "
                                                    "Usar precio promedio histórico XM o escenario "
                                                    "conservador. No es un ingreso regulado garantizado."),
                                            }.get(_enf_tipo,
                                                  "Ingresa el ingreso anual en MM COP/año directamente.")
                                            st.info(_tipo_nota, icon="ℹ️")

                                _col_a, _col_b = st.columns([5, 1], gap="small")
                                with _col_a:
                                    st.markdown(
                                        f'<div style="font-size:0.84rem;color:{EPM_SLATE};'
                                        f'font-weight:600;padding-top:6px">'
                                        f'{_sc_id} · {_sc_info.get("label","")}</div>'
                                        f'<div style="font-size:0.70rem;color:{EPM_GRAY};'
                                        f'margin-bottom:2px">{_plbl}</div>',
                                        unsafe_allow_html=True)
                                with _col_b:
                                    _val = st.number_input(
                                        f"Δ {_unit_v}", min_value=0.0, value=_prev_v,
                                        key=f"wi_db_{_letter}_{_sc_id}",
                                        label_visibility="collapsed")
                                _drv_new[_sc_id] = _val
                                if _val > 0:
                                    _has_val = True
                                    _ben_yr = _val * _price_v / 1e6
                                    st.markdown(
                                        f'<div style="font-size:0.72rem;color:{EPM_DARK};'
                                        f'margin-top:-4px;padding-left:2px">'
                                        f'↳ Beneficio anual estimado: '
                                        f'<b>{_ben_yr:,.2f} MM COP/año</b></div>',
                                        unsafe_allow_html=True)

                # ── FULL: parámetros de sensibilidad adicionales ───────────────
                if _w_model_level == "FULL":
                    st.divider()
                    st.markdown("##### 🔬 Parámetros de sensibilidad (nivel FULL)")
                    st.caption(
                        "El análisis FULL requiere rango de sensibilidad para WACC, beneficios y CAPEX. "
                        "Se generará un tornado chart en los resultados.")
                    _prev_sens = st.session_state.get("w_sensitivity",
                                                       {"wacc_pct": 2, "ben_pct": 20, "capex_pct": 10})
                    _sc1, _sc2, _sc3 = st.columns(3, gap="medium")
                    with _sc1:
                        _s_wacc = st.number_input(
                            "WACC ± (pp)", min_value=0.5, max_value=10.0,
                            value=float(_prev_sens.get("wacc_pct", 2)),
                            step=0.5, key=f"wi_sens_wacc_{_letter}",
                            help="Variación en puntos porcentuales sobre WACC base del config")
                    with _sc2:
                        _s_ben = st.number_input(
                            "Beneficios ± (%)", min_value=5.0, max_value=50.0,
                            value=float(_prev_sens.get("ben_pct", 20)),
                            step=5.0, key=f"wi_sens_ben_{_letter}",
                            help="Rango de variación sobre beneficios anuales estimados")
                    with _sc3:
                        _s_capex = st.number_input(
                            "CAPEX ± (%)", min_value=5.0, max_value=50.0,
                            value=float(_prev_sens.get("capex_pct", 10)),
                            step=5.0, key=f"wi_sens_capex_{_letter}",
                            help="Rango de variación sobre CAPEX ingresado")
                    # persist immediately so tornado can read on result page
                    st.session_state["w_sensitivity"] = {
                        "wacc_pct":  _s_wacc,
                        "ben_pct":   _s_ben,
                        "capex_pct": _s_capex,
                    }

            st.divider()
            _ab1, _ab2 = st.columns(2)
            with _ab1:
                if st.button("← Reiniciar Do Nothing",
                             use_container_width=True, key=f"wi_back4b_{_letter}"):
                    st.session_state.update({
                        "wizard_baseline_proj": None,
                        "w_current_alt_idx":    0,
                        "w_alts_done":          [],
                        "wizard_alt_projs":     [],
                    })
                    st.rerun()
            with _ab2:
                _is_last = (_w_cur_idx == _w_alt_cnt - 1)
                _btn_lbl = (f"✅ Agregar Alt. {_letter} y calcular Duelo"
                            if _is_last else f"Agregar Alt. {_letter} y continuar →")
                if st.button(_btn_lbl, type="primary",
                             use_container_width=True, key=f"wi_add_{_letter}",
                             disabled=not _residuals_ok,
                             help=None if _residuals_ok else
                             "Faltan valores residuales por declarar — sin valor por "
                             "defecto, cada componente debe declararse explícitamente."):
                    if not _residuals_ok:
                        st.warning(
                            "⚠️ Faltan valores residuales por declarar — completa todos "
                            "los componentes antes de continuar.")
                    elif not _has_val:
                        if _w_model_level == "RULE_BASED":
                            st.warning("⚠️ Use las opciones 'Escalar' o 'Rechazar' que aparecen arriba.")
                        elif _w_meth != "concept_rating":
                            st.warning("⚠️ Ingresa al menos un driver con valor > 0.")
                    else:
                        # ── Construir drivers (RULE_BASED usa lista vacía) ─────
                        if _w_model_level == "RULE_BASED":
                            _drivers_alt = []
                        else:
                            _vd_map_b: dict = {}
                            for _sc_id, _sc_info in _sc_active.items():
                                _delta = float(_drv_new.get(_sc_id, 0.0))
                                if _w_meth == "concept_rating":
                                    if _delta <= 3:
                                        continue
                                else:
                                    if _delta <= 0:
                                        continue
                                _vd_id_b = _sc_info.get("vd_id", "VD-1")
                                if _vd_id_b not in _vd_map_b:
                                    _vd_map_b[_vd_id_b] = {"reto": _sc_info.get("reto", ""), "scs": []}
                                _sc_type_b = "concept_rating" if _w_meth == "concept_rating" else "monetizable"
                                _vd_map_b[_vd_id_b]["scs"].append(SubCriterionInput(
                                    id=_sc_id,
                                    label=_sc_info.get("label", _sc_id),
                                    type=_sc_type_b,
                                    delta_annual=_delta,
                                    unit=_sc_info.get("unit", ""),
                                    price_per_unit=float(_sc_info.get("price_COP", 0) or 0),
                                    weight=1.0,
                                ))
                            _drivers_alt = [
                                DriverInput(id=_vid, reto=_d["reto"], sub_criteria=_d["scs"], weight=1.0)
                                for _vid, _d in _vd_map_b.items()
                            ]
                        _grp_id = st.session_state.get("w_duel_group_id",
                                                        f"DUEL-{_w_year}")
                        _ts_alt = datetime.now().strftime("%H%M%S")

                        # ── Construcción del RiskInput del componente principal (índice 0) ──
                        _bl_ctypes_snap       = getattr(_base_comp0, "consequence_types", {})
                        _bl_ctypes_probs_snap = getattr(_base_comp0, "consequence_type_probs", {})
                        _res_ctypes_snap      = locals().get("_d_residual_consequence_types", {})
                        _res_ctypes_probs_snap = locals().get("_d_residual_consequence_type_probs", {})

                        if _w_meth == "asset_aware":
                            # Asset-Aware: el tipo FINANCIERO se mantiene igual al baseline
                            # (ΔPoF×CoF fluye por driver EAL-evitada, no por movimiento matricial).
                            # Los tipos adicionales (H&S, Ambiental, etc.) usan el residual real.
                            _res_ctypes_merged = dict(_bl_ctypes_snap)   # copia baseline completo
                            _res_probs_merged  = dict(_bl_ctypes_probs_snap)
                            for _t_m, _v_m in _res_ctypes_snap.items():
                                if _t_m != "financiero":              # no sobreescribir financiero
                                    _res_ctypes_merged[_t_m] = _v_m
                            for _t_m, _v_m in _res_ctypes_probs_snap.items():
                                if _t_m != "financiero":
                                    _res_probs_merged[_t_m] = _v_m
                            _rrc_scalar = _base_comp0.baseline_consequence_level
                            _rrp_scalar = _base_comp0.baseline_probability_level
                        else:
                            _res_ctypes_merged = _res_ctypes_snap
                            _res_probs_merged  = _res_ctypes_probs_snap
                            _rrc_scalar = locals().get("_d_rrc", max(1, _base_comp0.baseline_consequence_level - 1))
                            _rrp_scalar = locals().get("_d_rrp", max(1, _base_comp0.baseline_probability_level - 1))

                        _risk_input_alt = RiskInput(
                            baseline_consequence_level=_base_comp0.baseline_consequence_level,
                            baseline_probability_level=_base_comp0.baseline_probability_level,
                            residual_consequence_level=_rrc_scalar,
                            residual_probability_level=_rrp_scalar,
                            consequence_types=_bl_ctypes_snap,
                            residual_consequence_types=_res_ctypes_merged,
                            consequence_type_probs=_bl_ctypes_probs_snap,
                            residual_consequence_type_probs=_res_probs_merged,
                            weibull_beta=_base_comp0.weibull_beta,
                            weibull_eta=_base_comp0.weibull_eta,
                            asset_age_years=_base_comp0.asset_age_years,
                            label=_base_comp0.label,
                            asset_type=_base_comp0.asset_type,
                            source_asset_id=_base_comp0.source_asset_id,
                        )

                        # ── D-Task18: componentes adicionales (índice 1+) declarados
                        # con la sección simplificada de Bloque 2b más arriba ──────────
                        _risk_components_alt = [_risk_input_alt] + list(_extra_risk_inputs_alt)

                        # ── PASO 1: proyecto provisional con optimal_year=execution_year ──
                        # Necesario para que evaluate_with_timing() calcule el año óptimo
                        # real desde las variables del proyecto (CAPEX, beneficios, ΔR,
                        # risk_growth_rate por BU, capex_inflation_rate de config).
                        _alt_proj_tmp = ProjectInput(
                            id=f"ALT{_letter}-{_w_year}-{_ts_alt}-tmp",
                            name=f"{_d_name} ({_letter})",
                            business_unit=_w_bu,
                            investment_type=_w_inv,
                            asset_type=_d_asset_type,
                            is_baseline=False,
                            exclusion_group_id=_grp_id,
                            valuation_methodology=_w_meth,
                            problem_description=st.session_state.get("w_problem_desc", ""),
                            costs=CostInput(
                                capex_COP=_d_capex * 1_000_000,
                                opex_incremental_COP_year=_d_opex * 1_000_000,
                                estimate_class=_d_estclass,
                            ),
                            execution_year=_w_year,
                            optimal_year=_w_year,   # provisional — será reemplazado
                            drivers=_drivers_alt,
                            risk_components=_risk_components_alt,
                            analysis_horizon_years=st.session_state.get("w_analysis_horizon_years") or 0,
                            construction_years=_d_constr,
                        )

                        # ── PASO 2: optimal_year emerge del motor (Copperleaf-fiel) ──
                        # evaluate_with_timing() simula VNT(t) para cada t en el horizonte,
                        # crece ΔR a risk_growth_rate por BU e infla CAPEX a
                        # capex_inflation_rate, devuelve el t* = argmax VNT(t).
                        _opt_yr_real = _w_year      # fallback si el cálculo falla
                        _opt_urg     = "AHORA"
                        _opt_timing_tmp: dict = {}
                        try:
                            _eng_timing  = ValueEngine(cfg_e)
                            _timing_res  = _eng_timing.evaluate_with_timing(_alt_proj_tmp)
                            _opt_timing_tmp = _timing_res.get("optimal_timing", {})
                            _opt_yr_real = _opt_timing_tmp.get("optimal_year", _w_year)
                            _opt_urg     = _opt_timing_tmp.get("urgency",      "AHORA")
                            # Para expansión con brecha calculada: tomar el más urgente
                            # entre el año óptimo del motor y el gap_year de capacidad
                            if (st.session_state.get("w_gap_computed", False)
                                    and _w_inv == "Crecimiento / Expansión"):
                                _gap_yr_alt = st.session_state.get("w_gap_year", _opt_yr_real)
                                _opt_yr_real = min(_opt_yr_real, _gap_yr_alt)
                        except Exception:
                            pass   # fallback silencioso → optimal_year = execution_year

                        # ── PASO 3: proyecto definitivo con optimal_year real ──────────
                        _alt_proj = ProjectInput(
                            id=f"ALT{_letter}-{_w_year}-{_ts_alt}",
                            name=f"{_d_name} ({_letter})",
                            business_unit=_w_bu,
                            investment_type=_w_inv,
                            asset_type=_d_asset_type,
                            is_baseline=False,
                            exclusion_group_id=_grp_id,
                            valuation_methodology=_w_meth,
                            problem_description=st.session_state.get("w_problem_desc", ""),
                            costs=CostInput(
                                capex_COP=_d_capex * 1_000_000,
                                opex_incremental_COP_year=_d_opex * 1_000_000,
                                estimate_class=_d_estclass,
                            ),
                            execution_year=_w_year,
                            optimal_year=_opt_yr_real,   # ← emerge del motor
                            drivers=_drivers_alt,
                            risk_components=_risk_components_alt,
                            analysis_horizon_years=st.session_state.get("w_analysis_horizon_years") or 0,
                            construction_years=_d_constr,
                        )

                        # ── PASO 4: fórmula maestra con CoD real ─────────────────────
                        # CoD = max(0, VNT(optimal_year) − VNT(execution_year))
                        # Ahora es > 0 cuando execution_year > optimal_year calculado.
                        _r_marg: dict = {}
                        _vnt_marg_mm  = 0.0
                        _vnt_alt_mm   = 0.0
                        _vnt_base_mm  = 0.0
                        _eav_marg_mm  = 0.0
                        _vei_eav_val  = 0.0
                        _alerta_marg  = None
                        _ben_mm       = 0.0
                        _risk_mm      = 0.0
                        _cod_mm       = 0.0
                        try:
                            _eng_marg  = ValueEngine(cfg_e)
                            _r_marg    = _eng_marg.evaluate_marginal(_alt_proj, _w_base_proj)
                            _vnt_marg_mm = _r_marg.get("vnt_marginal_COP",    0.0) / 1e6
                            _vnt_alt_mm  = _r_marg.get("vnt_alternative_COP", 0.0) / 1e6
                            _vnt_base_mm = _r_marg.get("vnt_baseline_COP",    0.0) / 1e6
                            _eav_marg_mm = _r_marg.get("eav_marginal_COP",    0.0) / 1e6
                            _vei_eav_val = _r_marg.get("vei_eav",              0.0)
                            _alerta_marg = _r_marg.get("alerta_pedagogica")
                            _r_alt_full  = _r_marg.get("alternative_result", {})
                            _ben_mm      = _r_alt_full.get("benefits_raw_COP",           0.0) / 1e6
                            _risk_mm     = _r_alt_full.get("delta_r_npv_COP",           0.0) / 1e6
                            _cod_mm      = (_r_alt_full.get("timing_analysis") or {}).get(
                                               "cod_total_COP", 0.0) / 1e6
                            _opex_npv_mm = _r_alt_full.get("opex_incremental_npv_COP",  0.0) / 1e6
                        except Exception as _emarg:
                            _alerta_marg = f"Error en evaluación: {_emarg}"
                            _opex_npv_mm = 0.0

                        _alts_done_new  = _w_done + [{
                            "letter":        _letter,
                            "name":          _d_name,
                            "capex_mm":      _d_capex,
                            "opex_mm":       _d_opex,
                            "opex_npv_mm":   _opex_npv_mm,
                            # Fórmula maestra completa (CoD ya es real)
                            "vnt_marg_mm":   _vnt_marg_mm,
                            "vnt_alt_mm":    _vnt_alt_mm,
                            "vnt_base_mm":   _vnt_base_mm,
                            "eav_marg_mm":   _eav_marg_mm,
                            "vei_eav":       _vei_eav_val,
                            "ben_mm":        _ben_mm,
                            "risk_mm":       _risk_mm,
                            "cod_mm":        _cod_mm,
                            "alerta":        _alerta_marg,
                            "meth":          _w_meth,
                            "reto_inputs":   _drv_new,
                            # Timing real por alternativa
                            "opt_yr":        _opt_yr_real,
                            "opt_urg":       _opt_urg,
                            "exec_yr":       _w_year,
                            "asset_type":    _d_asset_type,
                            "horizon_years": _h_used,
                        }]
                        _alt_projs_new = _w_alt_projs + [_alt_proj]
                        _upd: dict = {
                            "w_current_alt_idx":              _w_cur_idx + 1,
                            "w_alts_done":                     _alts_done_new,
                            "wizard_alt_projs":                _alt_projs_new,
                            f"w_alt_{_letter}_name":           _d_name,
                            f"w_alt_{_letter}_capex":          _d_capex,
                            f"w_alt_{_letter}_estclass":       _d_estclass,
                            f"w_alt_{_letter}_opex":           _d_opex,
                            f"w_alt_{_letter}_constr":         _d_constr,
                            f"w_alt_{_letter}_drivers":        _drv_new,
                            f"w_alt_{_letter}_rrc":            _d_rrc,
                            f"w_alt_{_letter}_rrp":            _d_rrp,
                            f"w_alt_{_letter}_asset_type":     _d_asset_type,
                        }
                        if _w_model_level == "RULE_BASED":
                            _upd[f"w_alt_{_letter}_checklist"] = _checklist_results
                        if _w_meth == "asset_aware":
                            _upd[f"w_alt_{_letter}_aw"] = {
                                "int_type": _int_type,
                                "age_new":  _aw_age_new,
                                "pof_new":  _pof_new,
                                "delta_pof":_delta_pof,
                                "eal_mm":   _aw_eal_mm,
                                # heredados del baseline
                                "beta":     _aw_beta,
                                "eta":      _aw_eta,
                                "age_bl":   _aw_age_bl,
                                "cof_mm":   _aw_cof,
                                "pof_bl":   _pof_now,
                            }
                        st.session_state.update(_upd)
                        st.rerun()

        # ── FASE C: Todas las alternativas ingresadas → Revisar y Calcular ────
        elif (_w_base_proj is not None
              and _w_cur_idx >= _w_alt_cnt
              and st.session_state.get("wizard_results") is None):
            _w_done_c    = st.session_state.get("w_alts_done", [])
            _alt_projs_c = st.session_state.get("wizard_alt_projs", [])

            st.markdown("#### ✅ Revisión antes del Duelo de Valor")
            st.markdown(
                f'<div style="background:{EPM_LIGHT};border-left:4px solid {EPM_DARK};'
                f'border-radius:0 8px 8px 0;padding:10px 16px;margin-bottom:14px;'
                f'font-size:0.80rem;color:{EPM_DEEP}">'
                f'<b>{len(_w_done_c)} alternativa(s) listas.</b> '
                f'El motor calculará el <b>VNT Marginal</b> de cada una vs. Do Nothing '
                f'y determinará la de mayor valor neto. Revisa antes de calcular.</div>',
                unsafe_allow_html=True)

            # ── Duelo de Escenarios: Caso Base + Alternativas lado a lado ────
            _review_cols = st.columns(len(_w_done_c) + 1, gap="medium") if _w_done_c else []

            # ── Tarjeta Do Nothing (caso base — columna 0) ────────────────────
            if _review_cols:
                with _review_cols[0]:
                    _dn_vnt_mm  = _w_done_c[0].get("vnt_base_mm", 0.0) if _w_done_c else 0.0
                    _dn_rc      = getattr(getattr(_w_base_proj, "risk", None),
                                          "baseline_consequence_level", 3)
                    _dn_rp      = getattr(getattr(_w_base_proj, "risk", None),
                                          "baseline_probability_level",  3)
                    _dn_name    = getattr(_w_base_proj, "project_name", "Sin intervención")
                    _dn_bu      = getattr(_w_base_proj, "business_unit", _w_bu)
                    _dn_capex   = getattr(
                        getattr(_w_base_proj, "costs", None), "capex_COP", 0) / 1e6
                    _dn_eav     = _w_done_c[0].get("eav_marg_mm", 0.0) if _w_done_c else 0.0
                    st.markdown(
                        f'<div style="background:#F8FAFC;border:2px dashed #94A3B8;'
                        f'border-radius:10px;padding:12px 14px;height:100%">'
                        # Cabecera
                        f'<div style="display:flex;justify-content:space-between;'
                        f'align-items:center;margin-bottom:6px">'
                        f'<span style="font-size:0.72rem;font-weight:700;color:{EPM_GRAY};'
                        f'letter-spacing:.05em">CASO BASE</span>'
                        f'<span style="font-size:0.65rem;background:#E2E8F0;'
                        f'padding:2px 7px;border-radius:10px;color:{EPM_SLATE}">⚠ Do Nothing</span>'
                        f'</div>'
                        f'<div style="font-size:0.82rem;font-weight:600;color:{EPM_SLATE};'
                        f'margin-bottom:10px;line-height:1.3">{_dn_name[:32]}</div>'
                        # VNT Bruto (referencia)
                        f'<div style="background:#F1F5F9;border-radius:6px;'
                        f'padding:8px 10px;margin-bottom:8px;text-align:center">'
                        f'<div style="font-size:0.62rem;color:{EPM_GRAY};font-weight:700;'
                        f'text-transform:uppercase">VNT Bruto (referencia)</div>'
                        f'<div style="font-size:1.15rem;font-weight:800;color:{EPM_SLATE}">'
                        f'{_dn_vnt_mm:+,.1f}<span style="font-size:0.7rem"> MM</span></div>'
                        f'<div style="font-size:0.60rem;color:{EPM_GRAY}">'
                        f'VNT_Marginal Alts. se restan a este valor</div></div>'
                        # Desglose
                        f'<div style="font-size:0.72rem;color:{EPM_SLATE};'
                        f'border-top:1px solid #E2E8F0;padding-top:7px;margin-bottom:6px">'
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                        f'<span>CAPEX invertido</span>'
                        f'<b style="color:{EPM_GRAY}">0 MM</b></div>'
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                        f'<span>Riesgo Consecuencia</span>'
                        f'<b style="color:{EPM_AMBER}">Nivel {_dn_rc}</b></div>'
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                        f'<span>Riesgo Probabilidad</span>'
                        f'<b style="color:{EPM_AMBER}">Nivel {_dn_rp}</b></div>'
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                        f'<span>OPEX incremental</span>'
                        f'<b style="color:{EPM_GRAY}">0 MM/a</b></div>'
                        f'</div>'
                        # Badges
                        f'<div style="display:flex;gap:8px;font-size:0.70rem">'
                        f'<div style="flex:1;background:#F1F5F9;border-radius:5px;'
                        f'padding:5px 7px;text-align:center">'
                        f'<div style="color:{EPM_GRAY};font-size:0.60rem">EAV Anual</div>'
                        f'<b style="color:{EPM_SLATE}">— ref.</b></div>'
                        f'<div style="flex:1;background:#F1F5F9;border-radius:5px;'
                        f'padding:5px 7px;text-align:center">'
                        f'<div style="color:{EPM_GRAY};font-size:0.60rem">VEI_EAV</div>'
                        f'<b style="color:{EPM_SLATE}">1.00×</b></div>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True)

            # ── Tarjetas alternativas (columnas 1..n) ─────────────────────────
            for _ci, (_ad, _rcol) in enumerate(zip(_w_done_c, _review_cols[1:])):
                with _rcol:
                    _vm  = _ad.get("vnt_marg_mm",  0.0)
                    _va  = _ad.get("vnt_alt_mm",   0.0)
                    _vb  = _ad.get("vnt_base_mm",  0.0)
                    _eav = _ad.get("eav_marg_mm",  0.0)
                    _vei = _ad.get("vei_eav",       0.0)
                    _ben = _ad.get("ben_mm",        0.0)
                    _rsk = _ad.get("risk_mm",       0.0)
                    _cod = _ad.get("cod_mm",        0.0)
                    _alt = _ad.get("alerta")
                    _vm_color  = EPM_DARK if _vm >= 0 else EPM_RED
                    _vm_icon   = "✅" if _vm >= 0 else "⚠️"
                    _meth_badge = {
                        "asset_aware":    "🔵 Asset-Aware",
                        "driver_based":   "🟢 Driver-Based",
                        "concept_rating": "🟡 Concept Rating",
                    }.get(_ad.get("meth",""), "⚪")
                    st.markdown(
                        f'<div style="background:#fff;border:2px solid '
                        f'{_vm_color if _vm != 0 else EPM_MID};'
                        f'border-radius:10px;padding:12px 14px">'
                        # Cabecera
                        f'<div style="display:flex;justify-content:space-between;'
                        f'align-items:center;margin-bottom:6px">'
                        f'<span style="font-size:0.72rem;font-weight:700;color:{EPM_DARK};'
                        f'letter-spacing:.05em">ALT. {_ad["letter"]}</span>'
                        f'<span style="font-size:0.65rem;color:{EPM_GRAY}">{_meth_badge}</span>'
                        f'</div>'
                        f'<div style="font-size:0.82rem;font-weight:600;color:{EPM_DEEP};'
                        f'margin-bottom:10px;line-height:1.3">{_ad["name"]}</div>'
                        # VNT Marginal — resultado principal
                        f'<div style="background:{_vm_color}12;border-radius:6px;'
                        f'padding:8px 10px;margin-bottom:8px;text-align:center">'
                        f'<div style="font-size:0.62rem;color:{EPM_GRAY};font-weight:700;'
                        f'text-transform:uppercase">VNT Marginal</div>'
                        f'<div style="font-size:1.15rem;font-weight:800;color:{_vm_color}">'
                        f'{_vm_icon} {_vm:+,.1f} MM</div>'
                        f'<div style="font-size:0.62rem;color:{EPM_GRAY}">'
                        f'VNT_Alt − VNT_Do Nothing</div></div>'
                        # Desglose fórmula maestra
                        f'<div style="font-size:0.72rem;color:{EPM_SLATE};'
                        f'border-top:1px solid #E2E8F0;padding-top:7px;margin-bottom:6px">'
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                        f'<span>Beneficios drivers</span>'
                        f'<b style="color:{EPM_DARK}">{_ben:+,.1f} MM</b></div>'
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                        f'<span>Riesgo mitigado (ΔR)</span>'
                        f'<b style="color:{EPM_TEAL}">{_rsk:+,.1f} MM</b></div>'
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                        f'<span>Cost of Delay</span>'
                        f'<b style="color:{"#DC2626" if _cod > 0 else EPM_GRAY}">'
                        f'{"−" if _cod > 0 else ""}{abs(_cod):,.1f} MM</b></div>'
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                        f'<span>CAPEX</span>'
                        f'<b style="color:{EPM_RED}">−{_ad["capex_mm"]:,.0f} MM</b></div>'
                        + (
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                        f'<span>ΔOPEX NPV</span>'
                        f'<b style="color:{"#059669" if _ad.get("opex_npv_mm",0)>=0 else EPM_RED}">'
                        f'{-_ad.get("opex_npv_mm",0.0):+,.1f} MM</b></div>'
                        if abs(_ad.get("opex_npv_mm", 0.0)) > 0.01 else
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                        f'<span style="color:{EPM_GRAY}">ΔOPEX NPV</span>'
                        f'<b style="color:{EPM_GRAY}">0.0 MM</b></div>'
                        ) +
                        f'</div>'
                        # EAV, VEI y timing del motor
                        f'<div style="display:flex;gap:6px;font-size:0.70rem;margin-bottom:6px">'
                        f'<div style="flex:1;background:#F0FDF4;border-radius:5px;'
                        f'padding:5px 7px;text-align:center">'
                        f'<div style="color:{EPM_GRAY};font-size:0.60rem">EAV Marginal</div>'
                        f'<b style="color:{EPM_DARK}">{_eav:+,.1f} MM/a</b></div>'
                        f'<div style="flex:1;background:#EFF6FF;border-radius:5px;'
                        f'padding:5px 7px;text-align:center">'
                        f'<div style="color:{EPM_GRAY};font-size:0.60rem">VEI_EAV</div>'
                        f'<b style="color:{EPM_BLUE}">{_vei:.2f}×</b></div>'
                        f'</div>'
                        # Fila de timing real (optimal_year emergente del motor)
                        + (lambda _oy, _ey, _ug: (
                            f'<div style="border-top:1px solid #E2E8F0;padding-top:6px;'
                            f'font-size:0.68rem;color:{EPM_SLATE}">'
                            f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                            f'<span>Año ejecución propuesto</span><b>{_ey}</b></div>'
                            f'<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
                            f'<span>Año óptimo (motor)</span>'
                            f'<b style="color:{"#DC2626" if _oy < _ey else EPM_DARK}">{_oy}</b></div>'
                            f'<div style="display:flex;justify-content:space-between">'
                            f'<span>Urgencia</span>'
                            f'<b style="color:{"#DC2626" if _ug=="AHORA" else "#D97706" if _ug=="PRONTO" else EPM_DARK}">'
                            f'{"🔴" if _ug=="AHORA" else "🟡" if _ug=="PRONTO" else "🟢"} {_ug}</b></div>'
                            f'</div>'))(
                            _ad.get("opt_yr", _ad.get("exec_yr", _w_year)),
                            _ad.get("exec_yr", _w_year),
                            _ad.get("opt_urg", "—"))
                        + f'</div>',
                        unsafe_allow_html=True)
                    # Alerta pedagógica si destruye valor
                    if _alt:
                        st.warning(_alt)

            st.markdown("")
            _fc1, _fc2 = st.columns(2)
            with _fc1:
                if st.button("← Corregir última alternativa",
                             use_container_width=True, key="wi_redo_last"):
                    _new_done  = _w_done_c[:-1]
                    _new_projs = _alt_projs_c[:-1]
                    st.session_state.update({
                        "w_current_alt_idx": len(_new_done),
                        "w_alts_done":        _new_done,
                        "wizard_alt_projs":   _new_projs,
                    })
                    st.rerun()
            with _fc2:
                if st.button("⚡ Calcular Duelo de Valor", type="primary",
                             use_container_width=True, key="wi_calc_duel"):
                    try:
                        _engine_d = ValueEngine(cfg_e)

                        # 1 — Evaluar todas las alternativas con fórmula maestra
                        _wr_list = []
                        for _alt_p in _alt_projs_c:
                            _mr = _engine_d.evaluate_marginal(_alt_p, _w_base_proj)
                            _wr_list.append(_mr)

                        # 2 — Identificar ganadora (máximo VNT marginal)
                        _winner_idx = max(
                            range(len(_wr_list)),
                            key=lambda i: _wr_list[i].get("vnt_marginal_COP", float("-inf")))
                        _winner_proj = _alt_projs_c[_winner_idx]

                        # 3 — Tiempo óptimo SOLO para la ganadora
                        _winner_timing = _engine_d.evaluate_with_timing(_winner_proj)

                        # 4 — Baseline
                        _r_base_eval = _engine_d.evaluate(_w_base_proj)

                        st.session_state["wizard_results"]        = _wr_list
                        st.session_state["wizard_r_base"]         = _r_base_eval
                        st.session_state["wizard_winner_idx"]     = _winner_idx
                        st.session_state["wizard_winner_timing"]  = _winner_timing

                        # Exportar para PLANEADOR modo Local
                        _all_proj = [_w_base_proj] + _alt_projs_c
                        _all_ev   = [_r_base_eval] + [m["alternative_result"] for m in _wr_list]
                        st.session_state["eval_results"] = _all_ev
                        st.session_state["projects"]     = _all_proj
                        # ── Brecha E: crear workflows en BORRADOR para proyectos nuevos ──
                        if "workflows" not in st.session_state:
                            st.session_state["workflows"] = {}
                        _wf_cycle_wiz = st.session_state.get(
                            "current_user", {}).get("cycle", "PAI-2026")
                        for _wp in _all_proj:
                            WorkflowEngine.ensure_workflow(
                                st.session_state["workflows"],
                                _wp.id, _wp.name, _wp.business_unit, _wf_cycle_wiz,
                            )
                        st.rerun()
                    except Exception as _ez:
                        st.error(f"❌ Error al calcular: {_ez}")
                        with st.expander("Detalle técnico"):
                            st.code(traceback.format_exc())

        # ══════════════════════════════════════════════════════════════════════
        # ── FASE D: THE STRATEGIC COCKPIT — Resultados del Duelo ─────────────
        # ══════════════════════════════════════════════════════════════════════
        elif st.session_state.get("wizard_results") is not None:
            _wizard_res    = st.session_state.get("wizard_results", [])
            _r_base        = st.session_state.get("wizard_r_base", {})
            _expert        = st.session_state.get("expert_mode", False)
            _w_model_level = st.session_state.get("w_model_level", "SIMPLIFIED")

            if not _wizard_res:
                st.warning("No hay resultados disponibles. Reinicia el Wizard.")
            elif _w_model_level == "RULE_BASED":
                # ──────────────────────────────────────────────────────────────
                # RULE_BASED: Tarjeta Go / No-Go simplificada (sin VNT cockpit)
                # ──────────────────────────────────────────────────────────────
                _rb_criteria_lbl = {
                    "c1": "Obligatorio por regulación/seguridad",
                    "c2": "Activo supera vida útil o condición crítica",
                    "c3": "Riesgo inminente de falla (≤ 12 meses)",
                    "c4": "OPEX actual > 30% del valor de reposición",
                    "c5": "Justificación técnica documentada",
                }
                _rb_alt_names = [
                    _mr.get("alternative_result", {}).get("project_name", f"Alt {chr(65+i)}")
                    for i, _mr in enumerate(_wizard_res)
                ]
                st.markdown("#### 📋 Resultado — Evaluación RULE_BASED")
                st.markdown(
                    f'<div style="background:#F0FDFA;border-left:4px solid {EPM_TEAL};'
                    f'border-radius:0 8px 8px 0;padding:10px 16px;margin-bottom:14px;'
                    f'font-size:0.80rem;color:#0F766E">'
                    f'Proyectos de <b>bajo CAPEX</b> con aprobación local. '
                    f'La decisión se basa en la lista de verificación técnica, '
                    f'no en VNT monetizado.</div>',
                    unsafe_allow_html=True)

                # Tarjeta por alternativa
                for _rbi, _mr in enumerate(_wizard_res):
                    _rb_letter  = chr(65 + _rbi)
                    _rb_name    = _mr.get("alternative_result", {}).get("project_name", f"Alt {_rb_letter}")
                    _rb_check   = st.session_state.get(f"w_alt_{_rb_letter}_checklist", {})
                    _rb_cnt     = sum(1 for v in _rb_check.values() if v)
                    _rb_go      = _rb_cnt >= 3
                    _rb_color   = EPM_DARK if _rb_go else EPM_RED
                    _rb_capex   = _mr.get("alternative_result", {}).get("capex_COP", 0) / 1e6
                    _rb_risk_red = (_mr.get("alternative_result", {}).get("delta_r_npv_COP", 0) / 1e6)

                    st.markdown(
                        f'<div style="background:{"#F0FDF4" if _rb_go else "#FFF5F5"};'
                        f'border:2px solid {_rb_color};border-radius:10px;'
                        f'padding:14px 18px;margin-bottom:12px">'
                        f'<div style="display:flex;justify-content:space-between;'
                        f'align-items:center;margin-bottom:8px">'
                        f'<span style="font-weight:700;color:{_rb_color};font-size:1.05rem">'
                        f'{"✅ GO" if _rb_go else "🚫 NO-GO"} — {_rb_name}</span>'
                        f'<span style="font-size:0.78rem;color:{EPM_GRAY}">'
                        f'CAPEX: {_rb_capex:,.0f} MM COP &nbsp;|&nbsp; '
                        f'ΔRiesgo: {_rb_risk_red:+,.1f} MM COP</span>'
                        f'</div>'
                        f'<div style="font-size:0.75rem;color:{EPM_SLATE}">'
                        + "".join(
                            f'{"✅" if _rb_check.get(cid, False) else "☐"} {clbl}<br>'
                            for cid, clbl in _rb_criteria_lbl.items()
                        )
                        + f'<b>Criterios cumplidos: {_rb_cnt}/5'
                        f'{"  ✔ Apto para aprobación local" if _rb_go else "  ✘ Requiere revisión adicional"}'
                        f'</b></div></div>',
                        unsafe_allow_html=True)

                # Nota de gobernanza
                st.info(
                    "📌 **Aprobación local:** Los proyectos RULE_BASED no requieren VNT monetizado. "
                    "El analista firma la lista de verificación y el jefe de área aprueba. "
                    "No se genera Reporte de Defensa VDM.")

                # Botones de navegación (simplificados)
                _rb_nav1, _rb_nav2 = st.columns(2)
                with _rb_nav1:
                    if st.button("◀ Ajustar Alternativas", use_container_width=True,
                                 key="wi_rb_back"):
                        st.session_state.update({
                            "w_current_alt_idx": 0, "w_alts_done": [],
                            "wizard_alt_projs": [], "wizard_results": None,
                            "wizard_r_base": None,
                        })
                        st.rerun()
                with _rb_nav2:
                    if st.button("↺ Nueva Evaluación", use_container_width=True,
                                 key="wi_rb_reset"):
                        for _k in [
                            "wizard_step","proyecto_mode","w_proj_name","w_problem_desc",
                            "w_bu","w_inv_type","w_exec_year","w_exec_year_idx",
                            "w_constr_yrs","w_alt_count","w_methodology","w_selected_vds",
                            "w_current_alt_idx","w_alts_done","wizard_alt_projs",
                            "wizard_baseline_proj","wizard_results","wizard_r_base",
                            "w_duel_group_id","w_model_level","w_capex_ref",
                        ]:
                            st.session_state.pop(_k, None)
                        st.session_state["wizard_step"] = 1
                        st.rerun()
            else:
                # ── Variables del ganador ─────────────────────────────────────
                _best_mr   = max(_wizard_res, key=lambda m: m.get("vnt_marginal_COP", -1e18))
                _best_alt  = _best_mr.get("alternative_result", {})
                _best_name = _best_alt.get("project_name", "—")

                # ══════════════════════════════════════════════════════════════
                # VEREDICTO — ¿la elección está resuelta por los datos?
                # Entre alternativas de una misma necesidad la incertidumbre
                # común (mercado, precios, mundo) las mueve a todas junto y se
                # cancela en la comparación. Lo que puede invertir el orden es
                # que una esté bien estimada y otra no. Por eso el veredicto se
                # construye sobre la banda de estimación de cada alternativa.
                # ══════════════════════════════════════════════════════════════
                _cfg_vd    = get_config()
                _eng_vd    = ValueEngine(_cfg_vd)
                _alt_projs_vd = st.session_state.get("wizard_alt_projs", []) or []
                _bands_vd  = []
                for _i_vd, _mr_vd in enumerate(_wizard_res):
                    _ar_vd = _mr_vd.get("alternative_result", {})
                    _pj_vd = _alt_projs_vd[_i_vd] if _i_vd < len(_alt_projs_vd) else None
                    if _pj_vd is None:
                        continue
                    try:
                        _bd_vd = _eng_vd.estimate_band(_pj_vd, base_result=_ar_vd)
                    except Exception:
                        continue
                    # El VNT marginal desplaza la banda: el ancho lo fija el CAPEX
                    _shift = (_mr_vd.get("vnt_marginal_COP", 0.0)
                              - _bd_vd["net_value_base_COP"])
                    _bands_vd.append({
                        "name":  _ar_vd.get("project_name", f"Alt {chr(65+_i_vd)}"),
                        "cls":   _bd_vd["estimate_class"],
                        "label": _bd_vd["label"],
                        "low":   _bd_vd["net_value_low_COP"]  + _shift,
                        "high":  _bd_vd["net_value_high_COP"] + _shift,
                        "base":  _mr_vd.get("vnt_marginal_COP", 0.0),
                        "band":  _bd_vd,
                    })

                if len(_bands_vd) >= 2:
                    _bands_vd.sort(key=lambda b: b["base"], reverse=True)
                    _b1, _b2 = _bands_vd[0], _bands_vd[1]
                    _ov = ValueEngine.overlap_fraction(
                        {"net_value_low_COP": _b1["low"], "net_value_high_COP": _b1["high"]},
                        {"net_value_low_COP": _b2["low"], "net_value_high_COP": _b2["high"]},
                    )
                    _thr = float(_cfg_vd.get("cost_estimate_uncertainty", {})
                                 .get("overlap_threshold", 0.0))
                    _resuelta = _ov <= _thr

                    _vd_bg  = "#ECFDF5" if _resuelta else "#FFFBEB"
                    _vd_bd  = EPM_GREEN if _resuelta else EPM_AMBER
                    _vd_tit = ("✅ DECISIÓN RESUELTA POR LOS DATOS" if _resuelta
                               else "⚠️ DECISIÓN NO RESUELTA POR LOS DATOS")
                    _vd_txt = (
                        f"La banda de <b>{_b1['name']}</b> no toca la de "
                        f"<b>{_b2['name']}</b>. La elección se sostiene en todo el rango "
                        f"de incertidumbre de estimación de ambas."
                        if _resuelta else
                        f"Las bandas de <b>{_b1['name']}</b> y <b>{_b2['name']}</b> se "
                        f"traslapan <b>{_ov*100:.0f}%</b>. Con la calidad actual de los "
                        f"estimativos el modelo no las distingue: el orden puede invertirse. "
                        f"Mejorar la base del estimativo, o dejar que el desempate lo haga el "
                        f"optimizador bajo restricción presupuestal — entre alternativas que "
                        f"empatan en valor, la más barata libera cupo para otras necesidades."
                    )
                    st.markdown(
                        f'<div style="background:{_vd_bg};border-left:4px solid {_vd_bd};'
                        f'border-radius:0 10px 10px 0;padding:12px 18px;margin-bottom:14px">'
                        f'<div style="font-weight:700;color:{_vd_bd};font-size:0.92rem;'
                        f'margin-bottom:4px">{_vd_tit}</div>'
                        f'<div style="font-size:0.78rem;color:{EPM_SLATE};line-height:1.55">'
                        f'{_vd_txt}</div></div>',
                        unsafe_allow_html=True)

                    # ── Gráfica de rangos de VNT por alternativa ──────────────
                    _fig_vd = go.Figure()
                    for _b in reversed(_bands_vd):
                        _fig_vd.add_trace(go.Bar(
                            y=[_b["name"][:34]],
                            x=[(_b["high"] - _b["low"]) / 1e6],
                            base=[_b["low"] / 1e6],
                            orientation="h",
                            marker_color=(EPM_GREEN if _b is _bands_vd[0] else EPM_TEAL),
                            marker_line=dict(width=0),
                            name=_b["cls"],
                            showlegend=False,
                            customdata=[[_b["low"]/1e6, _b["base"]/1e6,
                                         _b["high"]/1e6, _b["label"]]],
                            hovertemplate=(
                                "<b>%{y}</b><br>%{customdata[3]}<br>"
                                "VNT: %{customdata[0]:,.0f} … "
                                "<b>%{customdata[1]:,.0f}</b> … "
                                "%{customdata[2]:,.0f} MM COP<extra></extra>"
                            ),
                        ))
                        _fig_vd.add_trace(go.Scatter(
                            y=[_b["name"][:34]], x=[_b["base"] / 1e6],
                            mode="markers",
                            marker=dict(symbol="line-ns", size=18,
                                        line=dict(width=2.5, color="#0F172A")),
                            showlegend=False, hoverinfo="skip",
                        ))
                    _fig_vd.add_vline(x=0, line_dash="dot", line_color=EPM_GRAY)
                    _fig_vd.update_layout(
                        height=90 + 42 * len(_bands_vd),
                        xaxis_title="VNT marginal vs. no hacer nada (MM COP) — rango por base de estimativo",
                        margin=dict(t=10, b=50, l=10, r=20),
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(15,23,42,0.6)",
                        font=dict(family="Inter,Arial", size=11, color=EPM_SLATE),
                        bargap=0.45,
                    )
                    st.plotly_chart(_fig_vd, use_container_width=True)
                    st.caption(
                        "El ancho de cada barra viene de la **base del estimativo** declarada "
                        "para esa alternativa, no de un supuesto sobre el mundo. Lo que señala "
                        "riesgo de decidir mal no es que una barra sea ancha, sino que **se "
                        "crucen**: una barra ancha por encima de todas sigue siendo una "
                        "decisión clara. La marca vertical es el VNT puntual."
                    )

                _best_vmm  = _best_mr.get("vnt_marginal_COP", 0) / 1e6
                _best_eav  = _best_mr.get("eav_marginal_COP", 0) / 1e6
                _best_vei  = _best_mr.get("vei_eav", 0)
                _base_vmm  = _r_base.get("net_value_COP", 0) / 1e6
                _bar       = _best_mr.get("alternative_result", {})
                _ben       = _bar.get("benefits_raw_COP", 0) / 1e6
                _dr        = _bar.get("delta_r_npv_COP", 0) / 1e6
                _cod       = _bar.get("timing_analysis", {}).get("cod_total_COP", 0) / 1e6
                _cx        = _bar.get("capex_COP", 0) / 1e6
                _opex_npv  = _bar.get("opex_incremental_npv_COP", 0) / 1e6
                _vabs      = _bar.get("net_value_COP", 0) / 1e6
                _fpv       = _bar.get("factor_npv", 1.0)
                _wacc_r    = _bar.get("wacc", 0.10)
                _horz      = (_bar.get("horizon_asset_years")
                              or _bar.get("analysis_horizon_years", 10))
                # VD por Reto — extraídos de driver_results (evaluate() no devuelve vdN_COP)
                # Cada driver tiene id="VD-1"…"VD-6" y value_COP = beneficio descontado
                def _vd_cop(result_dict: dict, vd_id: str) -> float:
                    """Suma value_COP de todos los drivers con ese VD-ID."""
                    return sum(
                        d.get("value_COP", 0)
                        for d in result_dict.get("driver_results", [])
                        if d.get("id", "") == vd_id
                    )
                _vd1 = _vd_cop(_bar, "VD-1") / 1e6
                _vd2 = _vd_cop(_bar, "VD-2") / 1e6
                _vd3 = _vd_cop(_bar, "VD-3") / 1e6
                _vd4 = _vd_cop(_bar, "VD-4") / 1e6
                _vd5 = _vd_cop(_bar, "VD-5") / 1e6
                _vd6 = _vd_cop(_bar, "VD-6") / 1e6
                _winner_pos = _best_vmm > 0
                # SHA-256 de la sesión
                _sess_blob  = json.dumps({
                    "project": st.session_state.get("w_proj_name",""),
                    "bu": st.session_state.get("w_bu",""),
                    "year": st.session_state.get("w_exec_year",""),
                    "vnt_marg_mm": round(_best_vmm, 4),
                    "ts": datetime.now().isoformat()[:16],
                }, sort_keys=True)
                _sess_sha   = hashlib.sha256(_sess_blob.encode()).hexdigest()[:16]

                # ── VALUE HUD — Terminal financiera ───────────────────────────
                _hud_vmm_cls  = "positive" if _best_vmm > 0 else "negative"
                _hud_vei_cls  = "positive" if _best_vei >= 1 else ("warn" if _best_vei >= 0 else "negative")
                _hud_dr_cls   = "positive" if _dr > 0 else "neutral"
                _hud_cod_cls  = "negative" if _cod > 0 else "neutral"
                st.markdown(f"""
                <div class="value-hud">
                    <div class="hud-item" style="flex:0.6;padding-right:16px">
                        <span class="hud-label">Proyecto</span>
                        <span class="hud-value" style="font-size:0.8rem;color:#A7F3D0;
                        white-space:normal;text-align:center;line-height:1.3">
                            {st.session_state.get("w_proj_name","—")[:30]}</span>
                        <span class="hud-badge">{st.session_state.get("w_bu","")}</span>
                    </div>
                    <div class="hud-item">
                        <span class="hud-label">VNT Marginal</span>
                        <span class="hud-value {_hud_vmm_cls}">{_best_vmm:+,.1f} MM</span>
                        <span class="hud-badge">{"🏆 " + _best_name[:22] if _winner_pos else "⚠️ " + _best_name[:22]}</span>
                    </div>
                    <div class="hud-item">
                        <span class="hud-label">VNT Do Nothing</span>
                        <span class="hud-value neutral">{_base_vmm:+,.1f} MM</span>
                        <span class="hud-badge">Costo de inacción / año</span>
                    </div>
                    <div class="hud-item">
                        <span class="hud-label">ΔR — Riesgo Mitigado</span>
                        <span class="hud-value {_hud_dr_cls}">{_dr:+,.1f} MM</span>
                        <span class="hud-badge">CoD: {_cod:,.1f} MM</span>
                    </div>
                    <div class="hud-item">
                        <span class="hud-label">VEI<sub>EAV</sub></span>
                        <span class="hud-value {_hud_vei_cls}">{_best_vei:.2f}×</span>
                        <span class="hud-badge">EAV {_best_eav:,.1f} MM/año</span>
                    </div>
                    <div class="hud-item" style="flex:0.5">
                        <span class="hud-label">SHA-256</span>
                        <span class="hud-value" style="font-size:0.6rem;
                        color:#6EE7B7;letter-spacing:0.04em">{_sess_sha}</span>
                        <span class="hud-badge">ISO 55001 §4.5</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                # ── Alertas pedagógicas ───────────────────────────────────────
                for _mr in _wizard_res:
                    if _mr.get("alerta_pedagogica"):
                        st.warning(_mr["alerta_pedagogica"])

                # ── Layout principal: Cockpit 2 columnas ──────────────────────
                _col_left, _col_right = st.columns([3, 2], gap="large")

                with _col_left:
                    # ══════════════════════════════════════════════════════════
                    # MÓDULO 2 — DUELO DE ESCENARIOS  (Radar + Ranking)
                    # ══════════════════════════════════════════════════════════

                    # ── Determinar ganador con tiebreaker VEI ─────────────────
                    # Primario: VNT Marginal; desempate: VEI_EAV
                    _sorted_alts = sorted(
                        enumerate(_wizard_res),
                        key=lambda iv: (iv[1].get("vnt_marginal_COP", -1e18),
                                        iv[1].get("vei_eav", 0)),
                        reverse=True,
                    )
                    _winner_idx, _winner_mr = _sorted_alts[0]

                    # ── Badge ganador ──────────────────────────────────────────
                    _w_ar   = _winner_mr.get("alternative_result", {})
                    _w_vmm  = _winner_mr.get("vnt_marginal_COP", 0) / 1e6
                    _w_vei  = _winner_mr.get("vei_eav", 0)
                    _w_name = _w_ar.get("project_name", "—")
                    _badge_color = EPM_DARK if _w_vmm > 0 else EPM_RED
                    _badge_icon  = "🏆" if _w_vmm > 0 else "⚠️"
                    st.markdown(
                        f'<div class="glass-winner" style="padding:12px 18px;margin-bottom:14px;'
                        f'display:flex;align-items:center;gap:14px">'
                        f'<div style="font-size:1.6rem">{_badge_icon}</div>'
                        f'<div>'
                        f'<div style="font-size:0.58rem;font-weight:700;color:#6EE7B7;'
                        f'text-transform:uppercase;letter-spacing:.12em">Alternativa Ganadora · '
                        f'Alt {chr(65+_winner_idx)}</div>'
                        f'<div style="font-size:0.88rem;font-weight:700;color:#ECFDF5;'
                        f'line-height:1.3">{_w_name[:40]}</div>'
                        f'</div>'
                        f'<div style="margin-left:auto;text-align:right">'
                        f'<div style="font-size:1.3rem;font-weight:900;font-family:'
                        f"'JetBrains Mono',monospace;color:{'#4ADE80' if _w_vmm>0 else '#F87171'}\">"
                        f'{_w_vmm:+,.1f} MM</div>'
                        f'<div style="font-size:0.62rem;color:#A7F3D0">'
                        f'VEI {_w_vei:.2f}× (tiebreaker)</div>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True)

                    # ── Tiempo Óptimo de Intervención (solo ganadora) ─────────
                    _wt = st.session_state.get("wizard_winner_timing", {})
                    _ot = _wt.get("optimal_timing", {})
                    if _ot:
                        _ot_year   = _ot.get("optimal_year",        _w_year)
                        _ot_delay  = _ot.get("optimal_delay_years",  0)
                        _ot_delta  = _ot.get("delta_vs_t0",          0.0) / 1e6
                        _ot_urg    = _ot.get("urgency",              "AHORA")
                        _ot_rg     = _ot.get("risk_growth_rate",     0.15)
                        _ot_ci     = _ot.get("capex_inflation_rate", 0.04)
                        _ot_yr_res = _ot.get("yearly_results",       [])

                        _urg_cfg = {
                            "AHORA":    (EPM_RED,   "🔴", "Ejecutar este año — postergar destruye valor"),
                            "PRONTO":   (EPM_AMBER, "🟡", "Ejecutar el próximo año como máximo"),
                            "FLEXIBLE": (EPM_DARK,  "🟢", "Puede optimizarse sin pérdida significativa"),
                        }
                        _uc, _ui, _utxt = _urg_cfg.get(_ot_urg, _urg_cfg["AHORA"])

                        with st.expander(
                            f"⏱️ Tiempo Óptimo de Intervención — {_ui} {_ot_urg}  "
                            f"· Año recomendado: **{_ot_year}**", expanded=True):

                            _to1, _to2, _to3 = st.columns([1, 1, 2], gap="medium")
                            with _to1:
                                st.markdown(
                                    f'<div style="background:{_uc}15;border:2px solid {_uc};'
                                    f'border-radius:10px;padding:12px;text-align:center">'
                                    f'<div style="font-size:0.62rem;font-weight:700;color:{_uc};'
                                    f'text-transform:uppercase">Urgencia</div>'
                                    f'<div style="font-size:1.6rem">{_ui}</div>'
                                    f'<div style="font-size:0.82rem;font-weight:800;color:{_uc}">'
                                    f'{_ot_urg}</div>'
                                    f'<div style="font-size:0.65rem;color:{EPM_GRAY};margin-top:4px">'
                                    f'{_utxt}</div></div>',
                                    unsafe_allow_html=True)
                            with _to2:
                                _delay_txt = "Este año" if _ot_delay == 0 else f"+{_ot_delay} año{'s' if _ot_delay>1 else ''}"
                                _delta_color = EPM_DARK if _ot_delta >= 0 else EPM_RED
                                st.markdown(
                                    f'<div style="background:#F8FAFC;border:1.5px solid #E2E8F0;'
                                    f'border-radius:10px;padding:12px">'
                                    f'<div style="font-size:0.62rem;font-weight:700;color:{EPM_GRAY};'
                                    f'text-transform:uppercase;margin-bottom:6px">Análisis de postergación</div>'
                                    f'<div style="display:flex;justify-content:space-between;'
                                    f'font-size:0.72rem;color:{EPM_SLATE};margin-bottom:3px">'
                                    f'<span>Año planificado</span><b>{_w_year}</b></div>'
                                    f'<div style="display:flex;justify-content:space-between;'
                                    f'font-size:0.72rem;color:{EPM_SLATE};margin-bottom:3px">'
                                    f'<span>Año óptimo</span>'
                                    f'<b style="color:{_uc}">{_ot_year} ({_delay_txt})</b></div>'
                                    f'<div style="display:flex;justify-content:space-between;'
                                    f'font-size:0.72rem;color:{EPM_SLATE};margin-bottom:3px">'
                                    f'<span>Δ VNT vs. ejecutar hoy</span>'
                                    f'<b style="color:{_delta_color}">{_ot_delta:+,.1f} MM</b></div>'
                                    f'<div style="font-size:0.60rem;color:{EPM_GRAY};'
                                    f'border-top:1px solid #E2E8F0;padding-top:5px;margin-top:5px">'
                                    f'Tasa degradación riesgo: {_ot_rg:.0%}/año<br>'
                                    f'Inflación CAPEX: {_ot_ci:.0%}/año</div>'
                                    f'</div>',
                                    unsafe_allow_html=True)
                            with _to3:
                                if _ot_yr_res:
                                    _fig_ot = go.Figure()
                                    _ot_yrs  = [r["year"]          for r in _ot_yr_res]
                                    _ot_vnts = [r["net_value_COP"] / 1e6 for r in _ot_yr_res]
                                    _ot_cols = [
                                        EPM_DARK if r["year"] == _ot_year else
                                        ("#E2E8F0" if r["net_value_COP"] < 0 else "#93C5FD")
                                        for r in _ot_yr_res]
                                    _fig_ot.add_trace(go.Bar(
                                        x=[str(y) for y in _ot_yrs],
                                        y=_ot_vnts,
                                        marker_color=_ot_cols,
                                        text=[f"{v:+,.0f}" for v in _ot_vnts],
                                        textposition="outside",
                                        textfont=dict(size=9),
                                        hovertemplate="Año %{x}<br>VNT = %{y:,.1f} MM<extra></extra>",
                                    ))
                                    _fig_ot.add_annotation(
                                        x=str(_ot_year), y=1.0, yref="paper",
                                        text="▼ Óptimo",
                                        showarrow=False,
                                        font=dict(size=9, color=EPM_DARK),
                                        yanchor="bottom")
                                    _fig_ot.update_layout(
                                        height=200, margin=dict(l=0, r=0, t=24, b=0),
                                        plot_bgcolor="rgba(15,23,42,0.6)", paper_bgcolor="rgba(0,0,0,0)",
                                        showlegend=False,
                                        title=dict(text="VNT neto por año de ejecución",
                                                   font=dict(size=10), x=0),
                                        xaxis=dict(tickfont=dict(size=9), title=""),
                                        yaxis=dict(tickfont=dict(size=9),
                                                   title="MM COP", title_font=dict(size=9)),
                                    )
                                    st.plotly_chart(_fig_ot, use_container_width=True,
                                                    config={"displayModeBar": False})

                            # ── Curva de penalidad (A7 / La Sétima methodology) ──
                            # Fiel al gráfico central de Celso: la "V" alrededor del
                            # punto óptimo naranjo. Muestra el costo de ejecutar antes
                            # o después del año óptimo (en VNT no capturado).
                            if _ot_yr_res and len(_ot_yr_res) > 1:
                                _opt_vnt_pen = max(
                                    r["net_value_COP"] for r in _ot_yr_res
                                )
                                _pen_yrs  = [r["year"] for r in _ot_yr_res]
                                _pen_vals = [
                                    max(0.0, _opt_vnt_pen - r["net_value_COP"]) / 1e6
                                    for r in _ot_yr_res
                                ]
                                _fig_pen = go.Figure()
                                _fig_pen.add_trace(go.Scatter(
                                    x=[str(y) for y in _pen_yrs],
                                    y=_pen_vals,
                                    mode="lines+markers",
                                    line=dict(color=EPM_RED, width=2.5),
                                    marker=dict(
                                        size=[16 if y == _ot_year else 8
                                              for y in _pen_yrs],
                                        color=[EPM_AMBER if y == _ot_year
                                               else EPM_RED for y in _pen_yrs],
                                        symbol=["star" if y == _ot_year
                                                else "circle" for y in _pen_yrs],
                                    ),
                                    hovertemplate=(
                                        "Año %{x}<br>"
                                        "Penalidad: %{y:,.1f} MM COP"
                                        "<extra></extra>"
                                    ),
                                ))
                                _fig_pen.add_annotation(
                                    x=str(_ot_year), y=0,
                                    text=f"★ Óptimo {_ot_year}<br>Penalidad = 0",
                                    showarrow=True, arrowhead=2,
                                    ax=0, ay=-36,
                                    bgcolor=EPM_AMBER,
                                    font=dict(color="#fff", size=9),
                                    bordercolor=EPM_AMBER,
                                )
                                _fig_pen.update_layout(
                                    height=210,
                                    title=dict(
                                        text="Curva de Penalidad — Costo de ejecutar "
                                             "fuera del año óptimo (A7 / C55)",
                                        font=dict(size=10), x=0,
                                    ),
                                    xaxis_title="Año de ejecución",
                                    yaxis_title="Penalidad (MM COP)",
                                    margin=dict(t=30, b=40, l=60, r=20),
                                    paper_bgcolor="rgba(0,0,0,0)",
                                    plot_bgcolor="rgba(15,23,42,0.6)",
                                    font=dict(family="Inter,Arial", size=10,
                                              color=EPM_SLATE),
                                    showlegend=False,
                                )
                                st.plotly_chart(_fig_pen, use_container_width=True,
                                                config={"displayModeBar": False})
                                st.caption(
                                    "★ Punto óptimo (naranja) = penalidad cero. "
                                    "A la izquierda: costo de adelantar. "
                                    "A la derecha: costo de postergar. "
                                    "La asimetría revela si el proyecto tolera demoras."
                                )

                    # ── KPI cards: ranking compacto ───────────────────────────
                    _rank_cols = st.columns(len(_wizard_res) + 1, gap="small")
                    with _rank_cols[0]:
                        _dn_eav_mm = _r_base.get("eav_COP", 0) / 1e6
                        st.markdown(
                            f'<div style="background:#F8FAFC;border:1.5px dashed #CBD5E1;'
                            f'border-radius:8px;padding:10px;text-align:center">'
                            f'<div style="font-size:0.56rem;font-weight:700;color:{EPM_GRAY};'
                            f'text-transform:uppercase;letter-spacing:.08em">⚠ Do Nothing</div>'
                            f'<div style="font-size:1rem;font-weight:800;color:{EPM_SLATE}">'
                            f'{_base_vmm:+,.1f}<span style="font-size:0.62rem"> MM</span></div>'
                            f'<div style="font-size:0.58rem;color:{EPM_GRAY}">'
                            f'EAV {_dn_eav_mm:,.1f}/año</div>'
                            f'</div>', unsafe_allow_html=True)
                    for _rci, (_orig_i, _mr) in enumerate(_sorted_alts):
                        _ar   = _mr.get("alternative_result", {})
                        _vmm  = _mr.get("vnt_marginal_COP", 0) / 1e6
                        _capx = _ar.get("capex_COP", 0) / 1e6
                        _veie = _mr.get("vei_eav", 0)
                        _ib   = (_mr is _winner_mr)
                        _vc   = EPM_DARK if _vmm > 0 else EPM_RED
                        _rank_num = _rci + 1
                        with _rank_cols[_rci + 1]:
                            st.markdown(
                                f'<div style="background:{"#F0FDF4" if _ib else "#fff"};'
                                f'border:{"2px solid "+EPM_DARK if _ib else "1.5px solid #E2E8F0"};'
                                f'border-radius:8px;padding:10px;text-align:center">'
                                f'<div style="font-size:0.56rem;font-weight:700;color:{EPM_DARK};'
                                f'text-transform:uppercase;letter-spacing:.06em">'
                                f'{"🥇" if _rank_num==1 else "🥈" if _rank_num==2 else "🥉"} '
                                f'Alt {chr(65+_orig_i)}</div>'
                                f'<div style="font-size:0.7rem;font-weight:600;color:{EPM_DEEP};'
                                f'line-height:1.2;margin:3px 0">'
                                f'{_ar.get("project_name","?")[:22]}</div>'
                                f'<div style="font-size:1rem;font-weight:800;color:{_vc}">'
                                f'{_vmm:+,.1f}<span style="font-size:0.6rem"> MM</span></div>'
                                f'<div style="font-size:0.56rem;color:{EPM_GRAY};'
                                f'border-top:1px solid #F1F5F9;padding-top:4px;margin-top:4px">'
                                f'VEI {_veie:.2f}× · CAPEX {_capx:,.0f}</div>'
                                f'</div>', unsafe_allow_html=True)

                    # ── Radar Chart — 6 Retos normalizados ────────────────────
                    st.markdown("#### 🕸️ Radar de Valor — 6 Retos EPM (normalizado)")
                    st.caption("Cada eje se normaliza al máximo absoluto entre todas las alternativas. "
                               "Permite comparar contribución relativa por Reto.")

                    _radar_labels = [
                        "Reto 1\nCalidad", "Reto 2\nSvcs. Eficientes",
                        "Reto 3\nCobertura Univ.", "Reto 4\nCarbono Neutral.", "Reto 5\nGen. de Valor",
                        "Reto 6\nReputación"
                    ]
                    _VD_IDS = ["VD-1", "VD-2", "VD-3", "VD-4", "VD-5", "VD-6"]

                    # Recopilar valores por alternativa — desde driver_results
                    _radar_raw = []
                    for _mr in _wizard_res:
                        _ar = _mr.get("alternative_result", {})
                        _radar_raw.append([
                            _vd_cop(_ar, _vid) / 1e6 for _vid in _VD_IDS
                        ])

                    # Normalización: max absoluto por eje (evita divisón por 0)
                    _radar_maxes = [
                        max(abs(_radar_raw[i][j]) for i in range(len(_radar_raw))) or 1.0
                        for j in range(len(_VD_IDS))
                    ]
                    _radar_norm = [
                        [abs(v) / _radar_maxes[j] for j, v in enumerate(row)]
                        for row in _radar_raw
                    ]

                    _radar_colors_alt = [
                        "#00A86B", "#1E40AF", "#0F766E", "#D97706", "#7C3AED"
                    ]
                    _fig_radar = go.Figure()

                    # Do Nothing: cero en todos los ejes (referencia)
                    _fig_radar.add_trace(go.Scatterpolar(
                        r=[0.0] * len(_VD_IDS) + [0.0],
                        theta=_radar_labels + [_radar_labels[0]],
                        fill="toself",
                        fillcolor="rgba(100,116,139,0.06)",
                        line=dict(color="#CBD5E1", dash="dot", width=1.5),
                        name="Do Nothing",
                    ))

                    for _ri, ((_orig_i, _mr), _norm_row) in enumerate(
                        zip(_sorted_alts, [_radar_norm[i] for i, _ in _sorted_alts])
                    ):
                        _is_w = (_mr is _winner_mr)
                        _alt_col = _radar_colors_alt[_ri % len(_radar_colors_alt)]
                        _fig_radar.add_trace(go.Scatterpolar(
                            r=_norm_row + [_norm_row[0]],
                            theta=_radar_labels + [_radar_labels[0]],
                            fill="toself",
                            fillcolor=f"rgba({int(_alt_col[1:3],16)},"
                                      f"{int(_alt_col[3:5],16)},"
                                      f"{int(_alt_col[5:7],16)},0.12)",
                            line=dict(color=_alt_col, width=2.5 if _is_w else 1.5),
                            name=f"{'🏆 ' if _is_w else ''}Alt {chr(65+_orig_i)}",
                        ))

                    # ── Badge ganador sobre el radar ──────────────────────
                    _winner_letter = chr(65 + _sorted_alts[0][0]) if _sorted_alts else "?"
                    _winner_vei_v  = (_sorted_alts[0][1].get("vei_eav", 0) if _sorted_alts else 0)
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">'
                        f'<div style="background:linear-gradient(135deg,#007A3E,#00A86B);'
                        f'border-radius:12px;padding:8px 18px;font-size:0.82rem;font-weight:800;'
                        f'color:#fff;letter-spacing:0.02em">'
                        f'🏆 Alternativa {_winner_letter} — VEI {_winner_vei_v:.2f}×</div>'
                        f'<span style="font-size:0.75rem;color:#94A3B8">'
                        f'Radar normalizado 0–100% por reto corporativo</span></div>',
                        unsafe_allow_html=True)

                    _fig_radar.update_layout(
                        polar=dict(
                            radialaxis=dict(
                                visible=True, range=[0, 1],
                                tickvals=[0.25, 0.5, 0.75, 1.0],
                                ticktext=["25%", "50%", "75%", "100%"],
                                tickfont=dict(size=9, color="#94A3B8"),
                                gridcolor="rgba(255,255,255,0.08)",
                                linecolor="rgba(255,255,255,0.1)",
                                color="#94A3B8",
                            ),
                            angularaxis=dict(
                                tickfont=dict(size=10, color="#94A3B8"),
                                gridcolor="rgba(255,255,255,0.06)",
                                linecolor="rgba(255,255,255,0.08)",
                            ),
                            bgcolor="rgba(15,23,42,0.5)",
                        ),
                        showlegend=True,
                        legend=dict(
                            orientation="h", y=-0.16, font=dict(size=10, color="#94A3B8"),
                            bgcolor="rgba(15,23,42,0.6)", bordercolor="rgba(255,255,255,0.08)",
                        ),
                        height=360,
                        margin=dict(t=30, b=70, l=30, r=30),
                        paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(family="Inter,Arial", size=11, color="#94A3B8"),
                    )
                    st.plotly_chart(_fig_radar, use_container_width=True)

                    # ── Tabla comparativa completa ─────────────────────────────
                    with st.expander("📋 Tabla comparativa completa — todos los KPIs"):
                        _crow = [{
                            "": "⚠ Do Nothing",
                            "VNT Bruto (MM)": f"{_base_vmm:+,.1f}",
                            "VNT Marginal": "— ref.",
                            "CAPEX (MM)": "0",
                            "EAV/año (MM)": f"{_r_base.get('eav_COP',0)/1e6:,.1f}",
                            "VEI": "—",
                            "ΔR (MM)": "—",
                            "CoD (MM)": "—",
                        }]
                        for _rank_i, (_orig_i, _mr) in enumerate(_sorted_alts):
                            _ar = _mr.get("alternative_result", {})
                            _crow.append({
                                "": (f"{'🏆 ' if _rank_i==0 else ''}"
                                     f"Alt {chr(65+_orig_i)} (#{_rank_i+1})"),
                                "VNT Bruto (MM)": f"{_ar.get('net_value_COP',0)/1e6:+,.1f}",
                                "VNT Marginal": f"{_mr.get('vnt_marginal_COP',0)/1e6:+,.1f}",
                                "CAPEX (MM)": f"{_ar.get('capex_COP',0)/1e6:,.0f}",
                                "EAV/año (MM)": f"{_mr.get('eav_marginal_COP',0)/1e6:,.1f}",
                                "VEI": f"{_mr.get('vei_eav',0):.2f}×",
                                "ΔR (MM)": f"{_ar.get('delta_r_npv_COP',0)/1e6:+,.1f}",
                                "CoD (MM)": f"{_ar.get('timing_analysis',{}).get('cod_total_COP',0)/1e6:,.1f}",
                            })
                        _df_cmp = pd.DataFrame(_crow)
                        st.dataframe(_df_cmp, use_container_width=True, hide_index=True)

                    # ── Desglose por componente de inacción (por alternativa) ──
                    for _rank_i, (_orig_i, _mr) in enumerate(_sorted_alts):
                        _ar = _mr.get("alternative_result", {})
                        _componentes = _ar.get("risk_analysis", {}).get("components", [])
                        if len(_componentes) > 1:
                            with st.expander(
                                f"Desglose por componente de inacción "
                                f"({len(_componentes)}) — Alt {chr(65+_orig_i)}"
                            ):
                                st.dataframe(pd.DataFrame([
                                    {
                                        "Componente": c.get("label") or f"Componente {i+1}",
                                        "Activo de origen": c.get("source_asset_id") or "—",
                                        "ΔR (MM COP)": f"{c.get('delta_r_npv_COP', 0) / 1e6:+,.1f}",
                                    }
                                    for i, c in enumerate(_componentes)
                                ]), use_container_width=True, hide_index=True)

                with _col_right:
                    # ── Waterfall de construcción del VNT — Por Reto ─────────
                    st.markdown("#### ⚖️ Construcción del VNT — 6 Retos EPM")
                    st.caption("Cada beneficio deriva exclusivamente de los Retos Corporativos. "
                               "Sistema cerrado de valor — sin inductores externos.")
                    _VD_META_D = {
                        "Reto 1 · Calidad":             (_vd1, "#1E40AF"),
                        "Reto 2 · Svcs. Eficientes":    (_vd2, "#0F766E"),
                        "Reto 3 · Cobertura Univ.":     (_vd3, "#D97706"),
                        "Reto 4 · Carbono Neutral.":    (_vd4, "#059652"),
                        "Reto 5 · Gen. de Valor":       (_vd5, "#7C3AED"),
                        "Reto 6 · Reputación":          (_vd6, "#DB2777"),
                        "CoD (dif.)":            (-abs(_cod), "#DC2626"),
                        "ΔR Riesgo mit.":        (_dr,  "#00A86B"),
                        "CAPEX":                 (-abs(_cx),  "#1E293B"),
                    }
                    _wf_lbls = list(_VD_META_D.keys())
                    _wf_vals = [v for v, _ in _VD_META_D.values()]
                    _wf_cols = [c for _, c in _VD_META_D.values()]
                    _fig_wf  = go.Figure(go.Waterfall(
                        orientation="v",
                        measure=["relative"] * (len(_wf_lbls) - 1) + ["total"],
                        x=_wf_lbls[:-1] + ["VNT Total"],
                        y=_wf_vals[:-1] + [_vabs],
                        connector=dict(line=dict(color="#E2E8F0", width=1, dash="dot")),
                        increasing=dict(marker_color=EPM_DARK),
                        decreasing=dict(marker_color=EPM_RED),
                        totals=dict(marker_color=EPM_GREEN),
                        text=[f'{v:+,.0f}' for v in _wf_vals[:-1]] + [f'{_vabs:+,.0f}'],
                        textposition="outside",
                    ))
                    _fig_wf.add_hline(y=0, line_dash="dot", line_color="#CBD5E1")
                    _fig_wf.update_layout(
                        height=360, margin=dict(t=20,b=60,l=60,r=10),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(15,23,42,0.6)",
                        font=dict(family="Inter,Arial", size=10, color=EPM_SLATE),
                        yaxis=dict(title="MM COP", gridcolor="#F1F5F9", zeroline=False),
                        xaxis=dict(tickangle=-30),
                        showlegend=False)
                    st.plotly_chart(_fig_wf, use_container_width=True)

                    # Reto contribution bars (solo retos con valor)
                    _reto_data = [
                        ("Reto 1 — Calidad",              _vd1, "#1E40AF"),
                        ("Reto 2 — Svcs. Eficientes",     _vd2, "#0F766E"),
                        ("Reto 3 — Cobertura Univ.",      _vd3, "#D97706"),
                        ("Reto 4 — Carbono Neutral.",     _vd4, "#059652"),
                        ("Reto 5 — Gen. de Valor",        _vd5, "#7C3AED"),
                        ("Reto 6 — Reputación",           _vd6, "#DB2777"),
                    ]
                    _reto_total = sum(abs(v) for _, v, _ in _reto_data) or 1
                    _has_any = any(abs(v) > 0.01 for _, v, _ in _reto_data)
                    if _has_any:
                        for _rl, _rv, _rc in _reto_data:
                            if abs(_rv) < 0.01:
                                continue
                            _pct = abs(_rv) / _reto_total * 100
                            st.markdown(
                                f'<div style="margin-bottom:5px">'
                                f'<div style="display:flex;justify-content:space-between;'
                                f'font-size:0.72rem;color:{EPM_SLATE};margin-bottom:2px">'
                                f'<span style="font-weight:600">{_rl}</span>'
                                f'<span style="font-family:\'JetBrains Mono\',mono;color:{_rc}">'
                                f'{_rv:+,.1f} MM ({_pct:.0f}%)</span></div>'
                                f'<div style="height:5px;background:#F1F5F9;border-radius:3px">'
                                f'<div style="width:{_pct:.0f}%;height:100%;background:{_rc};'
                                f'border-radius:3px"></div></div></div>',
                                unsafe_allow_html=True)

                # ── Matriz de Riesgo comparativa: todas las alternativas ─────
                _d_wiz_base_proj = st.session_state.get("wizard_baseline_proj")
                _d_wiz_alt_projs = st.session_state.get("wizard_alt_projs", [])
                _d_bl_ctypes = (getattr(getattr(_d_wiz_base_proj, "risk", None),
                                        "consequence_types", {}) or {})
                _d_bl_cprobs = (getattr(getattr(_d_wiz_base_proj, "risk", None),
                                        "consequence_type_probs", {}) or {})
                if _d_bl_ctypes:
                    _d_alt_data_rm = []
                    _d_alt_colors_rm = [EPM_DARK, EPM_RED, "#7C3AED", "#D97706", "#15803D"]
                    for _d_ai, _d_ap in enumerate(_d_wiz_alt_projs):
                        _d_res_t = (getattr(getattr(_d_ap, "risk", None),
                                            "residual_consequence_types", {}) or {})
                        _d_res_p = (getattr(getattr(_d_ap, "risk", None),
                                            "residual_consequence_type_probs", {}) or {})
                        if _d_res_t:
                            _d_alt_data_rm.append({
                                "label":     f"Alt {chr(65+_d_ai)}",
                                "color":     _d_alt_colors_rm[_d_ai % len(_d_alt_colors_rm)],
                                "res_types": _d_res_t,
                                "res_probs": _d_res_p,
                            })
                    if _d_alt_data_rm:
                        st.markdown("---")
                        st.markdown("#### 🗺️ Matriz de Riesgo C55 — Todas las Alternativas")
                        st.caption(
                            "Círculos = posición baseline (Do Nothing). "
                            "Figuras = posición residual por alternativa. "
                            "Flechas muestran la reducción de riesgo lograda.")
                        _d_ctm_rm = epm_config.get("risk_matrix", {}).get("consequence_types_matrix", {})
                        _fig_rm_phase_d = _build_risk_matrix_fig(
                            bl_types=_d_bl_ctypes,
                            bl_probs=_d_bl_cprobs,
                            ctm_config=_d_ctm_rm,
                            title="Mapa de Riesgo — Baseline vs. Alternativas",
                            alt_data=_d_alt_data_rm,
                        )
                        st.plotly_chart(_fig_rm_phase_d, use_container_width=True,
                                        config={"displayModeBar": False})

                # ── THE TIME MACHINE — CoD bajo diferimiento ─────────────────
                st.markdown("---")
                st.markdown("#### 🕰️ The Time Machine — ¿Qué pasa si diferimos?")
                st.caption("El Costo del Diferimiento (CoD) crece con cada año de postergación. "
                           "El motor recalcula el VNT Marginal bajo el supuesto de riesgo creciente.")
                _defer_col, _chart_col = st.columns([1, 3], gap="medium")
                with _defer_col:
                    _defer_yrs = st.slider("Años de postergación", 0, 5, 0,
                                           key="tm_defer", label_visibility="visible")
                with _chart_col:
                    _tm_years = list(range(6))

                    # ── Usar yearly_results del motor (fuente de verdad) ──────
                    _wt = st.session_state.get("wizard_winner_timing") or {}
                    _ta_yr = (_wt.get("optimal_timing") or {}).get("yearly_results", [])

                    if _ta_yr and len(_ta_yr) >= 6:
                        # yearly_results[t] = VNT absoluto de la alternativa si se
                        # ejecuta con t años de deferral desde optimal_year.
                        # La marginal = VNT_alt(t) - VNT_DoNothing(constante).
                        _dn_vnt_mm = _r_base.get("net_value_COP", 0) / 1e6
                        _vnt_at_t0 = _ta_yr[0].get("net_value_COP", 0) / 1e6  # mejor año
                        # Offset: cuántos años ya lleva deferido vs optimal_year
                        _opt_yr_ta  = (_wt.get("optimal_timing") or {}).get("optimal_year",
                                        _bar.get("optimal_year", _bar.get("execution_year", 2026)))
                        _exec_yr_ta = _bar.get("execution_year",
                                       st.session_state.get("w_exec_year", 2026)) or 2026
                        _offset = max(0, int(_exec_yr_ta) - int(_opt_yr_ta))
                        _tm_vnt = []
                        for y in _tm_years:
                            idx = min(_offset + y, len(_ta_yr) - 1)
                            _abs_vnt = _ta_yr[idx].get("net_value_COP", 0) / 1e6
                            _tm_vnt.append(_abs_vnt - _dn_vnt_mm)
                    else:
                        # Fallback: descuento WACC + inflación CAPEX cuando
                        # yearly_results no está disponible (proyecto sin timing)
                        _wacc_fb = _wacc_r or 0.10
                        _cap_inf_fb = 0.04
                        _tm_vnt = [
                            _best_vmm / ((1 + _wacc_fb) ** y)
                            - _best_vmm * _cap_inf_fb * y  # costo CAPEX inflado
                            for y in _tm_years
                        ]

                    _tm_colors  = [EPM_DARK if v > 0 else EPM_RED for v in _tm_vnt]
                    _fig_tm     = go.Figure()
                    _fig_tm.add_trace(go.Bar(
                        x=_tm_years, y=_tm_vnt,
                        marker_color=_tm_colors,
                        text=[f'{v:+,.0f}' for v in _tm_vnt],
                        textposition="outside",
                        name="VNT Marginal proyectado"))
                    _fig_tm.add_trace(go.Scatter(
                        x=_tm_years, y=[0]*6,
                        mode="lines", line=dict(color="#94A3B8", dash="dot", width=1),
                        name="Umbral Do Nothing", showlegend=False))
                    if _defer_yrs > 0:
                        _tm_sel = _tm_vnt[_defer_yrs]
                        _fig_tm.add_annotation(
                            x=_defer_yrs, y=_tm_sel,
                            text=f"Año +{_defer_yrs}: {_tm_sel:+,.0f} MM",
                            showarrow=True, arrowhead=2,
                            bgcolor="#0F172A", font=dict(color="#ECFDF5", size=11),
                            bordercolor=EPM_GREEN)
                    _fig_tm.update_layout(
                        height=240, margin=dict(t=30,b=40,l=60,r=20),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(15,23,42,0.6)",
                        font=dict(family="Inter,Arial", size=11, color=EPM_SLATE),
                        yaxis=dict(title="VNT Marginal (MM COP)", gridcolor="#F1F5F9"),
                        xaxis=dict(title="Años de postergación", tickvals=list(range(6))),
                        showlegend=False)
                    st.plotly_chart(_fig_tm, use_container_width=True)
                    if _defer_yrs > 0:
                        _vnt_now  = _best_vmm
                        _vnt_def  = _tm_vnt[_defer_yrs]
                        _erosion  = _vnt_now - _vnt_def
                        st.markdown(
                            f'<div style="background:#FEF2F2;border-left:4px solid {EPM_RED};'
                            f'border-radius:0 6px 6px 0;padding:8px 14px;font-size:0.78rem;'
                            f'color:#1E293B">Diferir <b>{_defer_yrs} año(s)</b> erosiona el valor '
                            f'en <b style="color:{EPM_RED}">{_erosion:,.1f} MM COP</b> '
                            f'({_erosion/_vnt_now*100:.0f}% del VNT original).</div>'
                            if _vnt_now != 0 else '',
                            unsafe_allow_html=True)

                # ── Ecuación maestra (experto) ────────────────────────────────
                if _expert:
                    st.markdown("---")
                    st.markdown("#### 🔬 Ecuación Maestra — Detalle técnico")
                    # Verificar balance aritmético para detectar inconsistencias
                    _vabs_check = _ben - _cod + _dr - _cx - _opex_npv
                    _balance_ok = abs(_vabs_check - _vabs) < 0.5  # tolerancia 0.5 MM
                    _opex_tag   = (f' − <span style="color:#FB923C">{_opex_npv:,.1f}</span>'
                                   if abs(_opex_npv) > 0.01 else "")
                    _opex_lbl   = " − ΔOPEX_NPV" if abs(_opex_npv) > 0.01 else ""
                    st.markdown(
                        f'<div style="background:#0F172A;border:1px solid rgba(0,168,107,0.3);'
                        f'border-radius:8px;padding:14px 20px;font-family:\'JetBrains Mono\',monospace;'
                        f'font-size:0.82rem;color:#E2E8F0;line-height:1.9">'
                        f'<span style="color:#6EE7B7;font-weight:700">VNT_Marginal</span>'
                        f' = VNT_Alt − VNT_DoNothing'
                        f' = <b style="color:#4ADE80">{_best_vmm:+,.1f} MM COP</b><br>'
                        f'<span style="color:#6EE7B7;font-weight:700">VNT_Alt</span>'
                        f' = Σ(Reto 1-5)·FVP − CoD + ΔR − CAPEX{_opex_lbl}<br>'
                        f'&nbsp;&nbsp;= <span style="color:#60A5FA">{_ben:+,.1f}</span>'
                        f' − <span style="color:#F87171">{_cod:,.1f}</span>'
                        f' + <span style="color:#34D399">{_dr:+,.1f}</span>'
                        f' − <span style="color:#F87171">{_cx:,.1f}</span>'
                        f'{_opex_tag}'
                        f' = <b style="color:{"#4ADE80" if _vabs>=0 else "#F87171"}">'
                        f'{_vabs:+,.1f} MM COP</b>'
                        + ('' if _balance_ok else
                           f'&nbsp;<span style="color:#FCD34D;font-size:0.68rem">'
                           f'⚠ check Δ={_vabs-_vabs_check:+.1f}</span>') +
                        f'<br><span style="color:#94A3B8;font-size:0.70rem">'
                        f'FVP = {_fpv:.4f} &nbsp;·&nbsp; '
                        f'WACC = {_wacc_r:.1%} &nbsp;·&nbsp; '
                        + (
                            f'H = <span style="color:#6EE7B7">{_horz}a ✓ activo</span>'
                            if _bar.get("horizon_asset_years") and _bar.get("horizon_asset_years") != _bar.get("analysis_horizon_years", 10)
                            else f'H = {_horz}a'
                        ) +
                        f' &nbsp;·&nbsp; {_bar.get("badge_label","")}</span>'
                        f'</div>', unsafe_allow_html=True)

                # ── REPORTE DE DEFENSA — ISO 55001 §4.5 ─────────────────────
                st.markdown("---")
                _rdef_col, _goto_col, _new_col = st.columns(3, gap="medium")

                with _rdef_col:
                    _reto_lines = "\n".join([
                        f"  Reto {i+1}: {nm.split('·')[1].strip() if '·' in nm else nm}: "
                        f"{val:+,.1f} MM COP ({abs(val)/max(abs(_ben),1)*100:.0f}%)"
                        for i, (nm, val) in enumerate([
                            ("Reto 1 · Calidad",   _vd1),
                            ("Reto 2 · Eficiencia", _vd2),
                            ("Reto 3 · Cobertura",  _vd3),
                            ("Reto 4 · Carbono",    _vd4),
                            ("Reto 5 · Valor Reg.", _vd5),
                            ("Reto 6 · Reputación", _vd6),
                        ]) if abs(val) > 0.01
                    ])
                    # ── Badge de confianza para el reporte ───────────────────
                    _rep_af_key  = st.session_state.get("w_asset_family", "otro")
                    _rep_af      = ASSET_FAMILIES.get(_rep_af_key, ASSET_FAMILIES["otro"])
                    _rep_dl      = st.session_state.get("w_data_level", "driver_based")
                    _rep_pref    = _rep_af.get("metodologia_preferred", "driver_based")
                    _REP_HIER    = {"asset_aware": 3, "driver_based": 2, "concept_rating": 1}
                    _rep_gap     = _REP_HIER.get(_rep_pref, 2) - _REP_HIER.get(_rep_dl, 2)
                    _REP_CONF    = {0: "Alta confianza", 1: "Confianza media (fallback 1 nivel)",
                                    2: "Confianza mínima (fallback 2 niveles)"}
                    _rep_conf_str = _REP_CONF.get(_rep_gap, "Alta confianza")
                    _rep_aw_lines = ""
                    if _rep_dl == "asset_aware":
                        _rep_aw_data = st.session_state.get("w_alt_A_aw", {})
                        if _rep_aw_data:
                            _rep_aw_lines = (
                                f"\n**Parámetros Weibull (Alt. A):**  \n"
                                f"β = {_rep_aw_data.get('beta','—')} · "
                                f"η = {_rep_aw_data.get('eta','—')} años · "
                                f"Edad = {_rep_aw_data.get('age','—')} años · "
                                f"PoF = {_rep_aw_data.get('pof',0):.1%} · "
                                f"EAL = {_rep_aw_data.get('eal_mm',0):,.1f} MM COP/año\n"
                            )

                    _defense_md = (
                        f"# REPORTE DE DEFENSA DE INVERSIÓN\n"
                        f"**EPM — Motor AIP Enterprise**  \n"
                        f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}  \n"
                        f"SHA-256: `{_sess_sha}`  \n\n"
                        f"---\n"
                        f"## Proyecto\n"
                        f"**{st.session_state.get('w_proj_name','—')}**  \n"
                        f"BU: {st.session_state.get('w_bu','')} · "
                        f"Año: {st.session_state.get('w_exec_year','')} · "
                        f"Familia de activo: {_rep_af['icon']} {_rep_af['label']}  \n"
                        f"Metodología declarada: **{_rep_dl}** "
                        f"(preferida: {_rep_pref}) · "
                        f"Confianza del dato: **{_rep_conf_str}**"
                        f"{_rep_aw_lines}\n\n"
                        f"---\n"
                        f"## Decisión Recomendada\n"
                        f"**Alternativa ganadora:** {_best_name}  \n"
                        f"**VNT Marginal:** {_best_vmm:+,.1f} MM COP  \n"
                        f"**VNT Do Nothing (referencia):** {_base_vmm:+,.1f} MM COP  \n"
                        f"**VEI_EAV:** {_best_vei:.2f}×  \n"
                        f"**EAV Marginal:** {_best_eav:,.1f} MM COP/año\n\n"
                        f"---\n"
                        f"## Construcción del VNT — Sistema Cerrado EPM\n"
                        f"VNT = Σ(Retos 1-5) − CoD + ΔR − CAPEX  \n"
                        f"VNT = {_ben:+,.1f} − {_cod:,.1f} + {_dr:+,.1f} − {_cx:,.1f} "
                        f"= **{_vabs:+,.1f} MM COP**\n\n"
                        f"### Contribución por Reto Corporativo\n"
                        f"{_reto_lines}\n\n"
                        f"**Costo del Diferimiento (CoD):** {_cod:,.1f} MM COP  \n"
                        f"**Mitigación de Riesgo (ΔR):** {_dr:+,.1f} MM COP  \n"
                        f"**CAPEX:** {_cx:,.1f} MM COP\n\n"
                        f"---\n"
                        f"## Parámetros Financieros\n"
                        f"WACC: {_wacc_r:.1%} · Horizonte: {_horz} años"
                        + (" (específico del activo — IEEE/IEC/CREG)"
                           if _bar.get("horizon_asset_years") and _bar.get("horizon_asset_years") != _bar.get("analysis_horizon_years", 10)
                           else " (global por defecto)") +
                        f"  \nFVP: {_fpv:.4f}  \n"
                        f"{_bar.get('badge_label','')}\n\n"
                        f"---\n"
                        f"*Generado por EPM AIP Enterprise · ISO 55001:2024 §4.5*  \n"
                        f"*SHA-256 sesión: `{_sess_sha}`*"
                    )
                    if st.button("📄 Reporte de Defensa", use_container_width=True,
                                 key="wi_defense_report"):
                        st.download_button(
                            label="⬇ Descargar .md",
                            data=_defense_md,
                            file_name=f"EPM_AIP_Defensa_{st.session_state.get('w_proj_name','proyecto')[:20].replace(' ','_')}.md",
                            mime="text/markdown",
                            use_container_width=True,
                            key="wi_dl_report")
                        with st.expander("Vista previa del reporte"):
                            st.markdown(_defense_md)

                # ── FULL: Tornado de sensibilidad obligatorio ─────────────────
                if _w_model_level == "FULL":
                    st.markdown("---")
                    st.markdown("#### 🌪️ Análisis de Sensibilidad (Nivel FULL)")
                    st.caption(
                        "Obligatorio para nivel FULL (CAPEX ≥ umbral comité). "
                        "Muestra el impacto de variaciones en WACC, beneficios y CAPEX sobre el VNT Marginal.")
                    _sens_params  = st.session_state.get(
                        "w_sensitivity", {"wacc_pct": 2, "ben_pct": 20, "capex_pct": 10})
                    _s_wacc_r  = float(_sens_params.get("wacc_pct", 2))
                    _s_ben_r   = float(_sens_params.get("ben_pct", 20))
                    _s_capex_r = float(_sens_params.get("capex_pct", 10))
                    _vnt_base_full = _best_vmm   # ganador

                    # Construir escenarios ± para cada variable
                    _tornado_data = []
                    # WACC: más WACC → menor NPV → menor VNT; reducción de WACC → mejor VNT
                    # Approximation: ΔV/V ≈ -Δwacc × horizon / (1 + wacc)
                    _wacc_est = float(_bar.get("wacc", 0.10))
                    _horz_est = float(_bar.get("horizon_asset_years")
                                      or _bar.get("analysis_horizon_years", 10))
                    _wacc_factor_low  = 1 + (_s_wacc_r / 100) * _horz_est / (1 + _wacc_est)
                    _wacc_factor_high = 1 - (_s_wacc_r / 100) * _horz_est / (1 + _wacc_est)
                    _vnt_wacc_opt  = _vnt_base_full * _wacc_factor_low
                    _vnt_wacc_pes  = _vnt_base_full * _wacc_factor_high

                    # Beneficios: proporcional al rango
                    _vnt_ben_opt  = _vnt_base_full * (1 + _s_ben_r / 100)
                    _vnt_ben_pes  = _vnt_base_full * (1 - _s_ben_r / 100)

                    # CAPEX: mayor CAPEX → menor VNT (impacto directo en numerador)
                    _capex_share = _cx / max(abs(_vnt_base_full + _cx), 1)   # peso relativo
                    _vnt_capex_opt  = _vnt_base_full + _cx * (_s_capex_r / 100)
                    _vnt_capex_pes  = _vnt_base_full - _cx * (_s_capex_r / 100)

                    _tornado_rows = [
                        ("WACC",       _vnt_wacc_pes,  _vnt_base_full, _vnt_wacc_opt,  _s_wacc_r,  "pp"),
                        ("Beneficios", _vnt_ben_pes,   _vnt_base_full, _vnt_ben_opt,   _s_ben_r,   "%"),
                        ("CAPEX",      _vnt_capex_pes, _vnt_base_full, _vnt_capex_opt, _s_capex_r, "%"),
                    ]
                    _tornado_rows.sort(key=lambda r: abs(r[3] - r[1]), reverse=True)

                    def _hex_to_rgba(hex_color: str, alpha: float) -> str:
                        """Convierte #RRGGBB → rgba(r,g,b,a) para Plotly."""
                        h = hex_color.lstrip("#")
                        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                        return f"rgba({r},{g},{b},{alpha})"

                    _fig_tornado = go.Figure()
                    _colors_t = [EPM_DARK, EPM_TEAL, EPM_AMBER]
                    for _ti, (_tname, _tpes, _tbase, _topt, _trng, _tunit) in enumerate(_tornado_rows):
                        _tc = _colors_t[_ti % len(_colors_t)]
                        # Optimista
                        _fig_tornado.add_trace(go.Bar(
                            y=[f"<b>{_tname}</b> ±{_trng}{_tunit}"],
                            x=[_topt - _tbase],
                            base=[_tbase],
                            orientation="h",
                            name=f"{_tname} optimista",
                            marker_color=_hex_to_rgba(_tc, 0.80),
                            showlegend=(_ti == 0),
                            legendgroup="optimista",
                            legendgrouptitle_text="Optimista" if _ti == 0 else None,
                        ))
                        # Pesimista
                        _fig_tornado.add_trace(go.Bar(
                            y=[f"<b>{_tname}</b> ±{_trng}{_tunit}"],
                            x=[_tpes - _tbase],
                            base=[_tbase],
                            orientation="h",
                            name=f"{_tname} pesimista",
                            marker_color=_hex_to_rgba(EPM_RED, 0.40),
                            showlegend=(_ti == 0),
                            legendgroup="pesimista",
                            legendgrouptitle_text="Pesimista" if _ti == 0 else None,
                        ))
                    _fig_tornado.add_vline(
                        x=_vnt_base_full, line_dash="dot",
                        line_color=EPM_SLATE, line_width=1.5,
                        annotation_text=f"Base: {_vnt_base_full:+,.1f} MM",
                        annotation_position="top",
                        annotation_font_size=10)
                    _fig_tornado.update_layout(
                        barmode="relative", height=260,
                        margin=dict(l=120, r=20, t=30, b=30),
                        xaxis_title="VNT Marginal (MM COP)",
                        xaxis=dict(gridcolor="#E2E8F0"),
                        yaxis=dict(gridcolor="rgba(0,0,0,0)"),
                        plot_bgcolor="rgba(15,23,42,0.6)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(size=11),
                        legend=dict(orientation="h", y=-0.25),
                    )
                    st.plotly_chart(_fig_tornado, use_container_width=True,
                                    config={"displayModeBar": False})
                    st.caption(
                        f"Base: **{_vnt_base_full:+,.1f} MM COP** | "
                        f"WACC ±{_s_wacc_r} pp | Beneficios ±{_s_ben_r}% | CAPEX ±{_s_capex_r}%")

                with _goto_col:
                    if st.button("◀ Ajustar Alternativas", use_container_width=True,
                                 key="wi_back_alts"):
                        st.session_state.update({
                            "w_current_alt_idx": 0, "w_alts_done": [],
                            "wizard_alt_projs": [], "wizard_results": None,
                            "wizard_r_base": None,
                        })
                        st.rerun()

                with _new_col:
                    if st.button("↺ Nueva Evaluación", use_container_width=True,
                                 key="wi_reset_duel"):
                        for _k in [
                            "wizard_step","proyecto_mode","w_proj_name","w_problem_desc",
                            "w_bu","w_inv_type","w_exec_year","w_exec_year_idx",
                            "w_constr_yrs","w_alt_count","w_methodology","w_selected_vds",
                            "w_current_alt_idx","w_alts_done","wizard_alt_projs",
                            "wizard_baseline_proj","wizard_results","wizard_r_base","w_duel_group_id",
                        ]:
                            st.session_state.pop(_k, None)
                        st.session_state["wizard_step"] = 1
                        st.rerun()

                # ── Brecha E: Estado workflow + botón Someter ─────────────
                _winner_proj_ids = [
                    p.id for p in st.session_state.get("projects", [])
                    if not getattr(p, "is_baseline", False)
                ]
                _wf_store_e = st.session_state.get("workflows", {})
                _cu_e = st.session_state.get("current_user", {})
                if _winner_proj_ids and _wf_store_e:
                    st.markdown("---")
                    st.markdown("##### 📋 Estado de aprobación — Brecha E")
                    for _pid in _winner_proj_ids[:3]:  # máx 3 proyectos en el wizard
                        _wf_e = _wf_store_e.get(_pid)
                        if not _wf_e:
                            continue
                        _sc = WF_STATE_COLOR.get(_wf_e.current_state, "#94A3B8")
                        _sl = WF_STATES.get(_wf_e.current_state, _wf_e.current_state)
                        st.markdown(
                            f'<div style="display:flex;align-items:center;gap:10px;'
                            f'padding:6px 10px;background:{_sc}18;border-left:3px solid {_sc};'
                            f'border-radius:4px;margin-bottom:6px">'
                            f'<span style="color:{_sc};font-weight:700">{_sl}</span>'
                            f'<span style="color:#94A3B8;font-size:12px">· {_wf_e.project_name[:40]}</span>'
                            f'</div>', unsafe_allow_html=True)

                        # Botón de acción rápida (la primera acción válida para el rol)
                        if _cu_e.get("name"):
                            _quick_actions = WorkflowEngine.valid_actions(
                                _cu_e.get("role", "analista"), _wf_e.current_state)
                            if _quick_actions:
                                _qa = _quick_actions[0]
                                if st.button(
                                    f"{_qa['icon']} {_qa['label']} — {_wf_e.project_name[:25]}",
                                    key=f"wf_quick_{_pid}",
                                    use_container_width=True,
                                ):
                                    try:
                                        WorkflowEngine.apply(
                                            wf=_wf_e,
                                            action=_qa,
                                            actor=_cu_e["name"],
                                            role=_cu_e["role"],
                                            bu=_cu_e.get("bu", ""),
                                            **_closure_kwargs_for(_pid, _qa),
                                        )
                                        st.session_state["workflows"][_pid] = _wf_e
                                        st.success(
                                            f"✅ {_qa['label']} aplicado → "
                                            f"{WF_STATES.get(_wf_e.current_state,'')}"
                                        )
                                        st.rerun()
                                    except ValueError as _ve:
                                        st.error(str(_ve))
                        else:
                            st.caption("Confirma tu identidad en el sidebar para ejecutar acciones.")

                # ── Footer SHA-256 ────────────────────────────────────────────
                st.markdown(
                    f'<div style="margin-top:16px;display:flex;align-items:center;'
                    f'justify-content:space-between;flex-wrap:wrap;gap:8px">'
                    f'<div class="sha-badge">🔐 SHA-256 &nbsp;{_sess_sha}</div>'
                    f'<div style="font-size:0.62rem;color:{EPM_GRAY}">'
                    f'EPM AIP Enterprise · ISO 55001:2024 §4.5 · '
                    f'{datetime.now().strftime("%d/%m/%Y %H:%M")}</div>'
                    f'</div>',
                    unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB PLANEADOR — §1 Portafolio  (v12.2: fusionado en PLANEADOR)
# ══════════════════════════════════════════════════════════════════════════════
with _tab_plan:
    page_header("Portafolio de Inversiones",
                "Carga, análisis y visualización del portafolio EPM",
                "ISO 55001:2024 §4.5")

    ev = st.session_state["eval_results"]
    projects = st.session_state["projects"]

    if not ev:
        # ── Sin portafolio: guiar sin bloquear ───────────────────────────────
        col_empty1, col_empty2 = st.columns([1, 1], gap="large")
        with col_empty1:
            st.markdown("""
<div style="background:#F0FDF4;border:1px solid #A7F3D0;border-radius:10px;
     padding:24px;text-align:center">
  <div style="font-size:1.8rem;margin-bottom:10px">📂</div>
  <div style="font-weight:700;color:#005C2E;margin-bottom:6px">Cargar portafolio Excel/CSV</div>
  <div style="font-size:0.78rem;color:#374151;margin-bottom:14px">
    Arrastra tu archivo en el <b>panel lateral izquierdo</b>.<br>
    Soporta T&D, TI/Empresariales, AyA, Gas y Generación.
  </div>
</div>""", unsafe_allow_html=True)
            with st.expander("📋 Formato esperado del archivo"):
                st.markdown("""
| Campo | Ejemplos de nombre aceptado |
|---|---|
| ID del proyecto | `id`, `codigo`, `Código Proyecto Banco PeI` |
| Nombre | `nombre`, `name`, `Nombre Proyecto PeI` |
| Unidad de Negocio | `negocio`, `segment`, `Segmento` |
| CAPEX 2026 | `capex_2026`, `2026`, `CAPEX 2026` |
| ENS MWh/año | `delta_ens`, `ens`, `mwh_año` |
| Agua m³/año | `delta_agua`, `m3_año` |

**Formatos de valor aceptados:** `42.867`, `42867`, `42,867.00`, `42.867MM`
                """)
        with col_empty2:
            st.markdown("""
<div style="background:#EFF6FF;border:1px solid #BFDBFE;border-radius:10px;
     padding:24px;text-align:center">
  <div style="font-size:1.8rem;margin-bottom:10px">🔬</div>
  <div style="font-weight:700;color:#1E40AF;margin-bottom:6px">O evalúa sin archivo</div>
  <div style="font-size:0.78rem;color:#374151;margin-bottom:14px">
    Usa la pestaña <b>🔬 PROYECTO</b> para evaluar alternativas
    desde cero con el Wizard de 4 Pasos.<br>
    No requiere Excel.
  </div>
</div>""", unsafe_allow_html=True)
        # No st.stop() — la tab sigue renderizando con contenido vacío
        st.info("Una vez cargado el portafolio, los gráficos y tablas aparecerán aquí.")
        # Saltar el resto del tab (sin bloquear la app globalmente)
        ev = []

    # ── KPIs ─────────────────────────────────────────────────────────────────
    valid = [r for r in ev if "error" not in r]
    if not valid:
        pass  # empty state already handled above
    else:
        total_capex = sum(r["capex_COP"] for r in valid)
        total_vnt   = sum(r["net_value_COP"] for r in valid)
        n_pos = sum(1 for r in valid if r["net_value_COP"] > 0)
        n_neg = sum(1 for r in valid if r["net_value_COP"] < 0)
        n_mand= sum(1 for r in valid if r.get("is_mandatory"))
        avg_vei = (sum(r.get("vei", r.get("vei_eav", 0)) for r in valid
                    if r.get("vei", r.get("vei_eav", 0)) > 0) / max(1, n_pos))

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1: metric_card("Proyectos", str(len(valid)),
                              f"{n_pos} VNT+ · {n_neg} VNT−")
        with c2: metric_card("CAPEX Total", fmt_cop(total_capex), "2026-2030")
        with c3: metric_card("VNT Portafolio", fmt_cop(total_vnt),
                              "Valor Neto Total", EPM_TEAL)
        with c4: metric_card("VEI Promedio", f"{avg_vei:.2f}×", "Índice Eficiencia")
        with c5: metric_card("Obligatorios", str(n_mand),
                              "Must-do regulatorio", EPM_AMBER)

        st.divider()

    # ── Filtros (safe with empty valid — downstream guards handle it) ──────────
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        bus = ["Todos"] + sorted(set(r["business_unit"] for r in valid))
        bu_filter = st.selectbox("Unidad de Negocio", bus, key="bu_filter")
    with col_f2:
        tipos = ["Todos"] + sorted(set(r.get("investment_type","") for r in valid))
        tipo_filter = st.selectbox("Portafolio / Tipo", tipos, key="tipo_filter")
    with col_f3:
        vnt_filter = st.radio("Filtro VNT", ["Todos","Solo positivos","Solo negativos"],
                               horizontal=True, key="vnt_filter")

    filtered = valid
    if bu_filter != "Todos":
        filtered = [r for r in filtered if r["business_unit"] == bu_filter]
    if tipo_filter != "Todos":
        filtered = [r for r in filtered if r.get("investment_type") == tipo_filter]
    if vnt_filter == "Solo positivos":
        filtered = [r for r in filtered if r["net_value_COP"] > 0]
    elif vnt_filter == "Solo negativos":
        filtered = [r for r in filtered if r["net_value_COP"] <= 0]

    # ── Treemap de valor ──────────────────────────────────────────────────────
    section_bar("🗺️ Treemap — Valor por Proyecto y Unidad de Negocio")
    if filtered:
        tm_data = [r for r in filtered if r["net_value_COP"] > 0]
        if tm_data:
            fig_tm = go.Figure(go.Treemap(
                labels=[r["project_name"][:35] for r in tm_data],
                parents=[r["business_unit"] for r in tm_data],
                values=[r["net_value_COP"] / 1e6 for r in tm_data],
                customdata=[[
                    r["project_id"],
                    r.get("vei", r.get("vei_eav", 0)),
                    r["capex_COP"]/1e6,
                    r.get("is_mandatory",""),
                ] for r in tm_data],
                hovertemplate=(
                    "<b>%{label}</b><br>"
                    "ID: %{customdata[0]}<br>"
                    "VNT: $%{value:.0f} MM<br>"
                    "VEI: %{customdata[1]:.2f}×<br>"
                    "CAPEX: $%{customdata[2]:.0f} MM<br>"
                    "<extra></extra>"
                ),
                texttemplate="<b>%{label}</b><br>$%{value:.0f}MM",
                marker=dict(
                    colorscale=[[0,"#C5E8D4"],[0.5,"#007A3E"],[1,"#003D1E"]],
                    colorbar=dict(title="VNT (MM COP)"),
                    cmin=0,
                    cmax=max(r["net_value_COP"]/1e6 for r in tm_data),
                ),
                tiling=dict(packing="squarify"),
            ))
            fig_tm.update_layout(
                height=480, margin=dict(t=20,l=0,r=0,b=0),
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(family="IBM Plex Sans, sans-serif",
                          size=11, color="#1E293B"),
            )
            st.plotly_chart(fig_tm, use_container_width=True)
        else:
            st.info("Ningún proyecto con VNT positivo en el filtro actual.")

    # ── Scatter VNT vs CAPEX ──────────────────────────────────────────────────
    section_bar("📈 VNT vs. CAPEX por Proyecto")
    if filtered:
        fig_sc = px.scatter(
            x=[r["capex_COP"]/1e6 for r in filtered],
            y=[r["net_value_COP"]/1e6 for r in filtered],
            color=[r["business_unit"] for r in filtered],
            size=[max(1, abs(r["net_value_COP"])/1e6) for r in filtered],
            hover_name=[r["project_name"][:40] for r in filtered],
            hover_data={
                "VEI": [f'{r.get("vei", r.get("vei_eav", 0)):.2f}×' for r in filtered],
                "ID":  [r["project_id"] for r in filtered],
            },
            labels={"x":"CAPEX (MM COP)","y":"VNT (MM COP)","color":"BU"},
            color_discrete_map={"TyD":"#007A3E","AyA":"#1E40AF","Gas":"#D97706","Generacion":"#0F766E","Todos":"#94A3B8"},
        )
        fig_sc.add_hline(y=0, line_dash="dash", line_color="#DC2626",
                          annotation_text="VNT = 0")
        fig_sc.update_layout(height=400, margin=dict(t=20,b=40),
                               paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_sc, use_container_width=True)

    # ── Tabla ─────────────────────────────────────────────────────────────────
    st.subheader(f"📋 Tabla de Proyectos ({len(filtered)} mostrados)")
    if filtered:
        df = pd.DataFrame([{
            "ID":             r["project_id"],
            "Nombre":         r["project_name"][:45],
            "BU":             r["business_unit"],
            "CAPEX MM":       round(r["capex_COP"]/1e6, 1),
            "VNT MM":         round(r["net_value_COP"]/1e6, 1),
            "VEI":            round(r.get("vei", r.get("vei_eav", 0)), 3),
            "Nivel":          r.get("model_level","?"),
            "Año Óptimo":     r.get("optimal_year","?"),
            "Obligatorio":    "★" if r.get("is_mandatory") else "",
        } for r in sorted(filtered, key=lambda x: x["net_value_COP"], reverse=True)])

        def color_vnt(val):
            if isinstance(val, (int,float)):
                if val > 0:   return f"color: {EPM_DARK}; font-weight:600"
                elif val < 0: return f"color: {EPM_RED};  font-weight:600"
            return ""

        _styler = _style_apply(df.style, color_vnt, subset=["VNT MM"])
        _styler = _styler.format({"CAPEX MM":"{:,.1f}","VNT MM":"{:,.1f}","VEI":"{:.3f}"})
        st.dataframe(_styler, use_container_width=True, height=420)

    # ── Advertencias de carga ─────────────────────────────────────────────────
    rep = st.session_state.get("portfolio_report", {})
    if rep.get("warnings"):
        with st.expander(f"⚠️ {len(rep['warnings'])} advertencia(s) de carga"):
            for w in rep["warnings"]:
                st.caption(f"• {w}")
    if rep.get("unmapped"):
        with st.expander(f"📌 {len(rep['unmapped'])} columna(s) no mapeadas"):
            st.caption("Estas columnas del archivo no fueron reconocidas:")
            for u in rep["unmapped"]:
                st.code(u, language=None)

# ══════════════════════════════════════════════════════════════════════════════
# TAB PLANEADOR — §2 Optimizador MILP v8.4  (v12.2: fusionado en PLANEADOR)
# ══════════════════════════════════════════════════════════════════════════════
with _tab_plan:
    page_header("Optimizador de Portafolio",
                "MILP Enterprise v8.4 · Local · Silo de Negocio · Enterprise Global",
                "Copperleaf C55 §5 · ISO 55001:2024 §4.5.4")

    cfg = get_config()
    projects = st.session_state["projects"]

    if not projects:
        col_opt_e1, col_opt_e2 = st.columns([1, 1], gap="large")
        with col_opt_e1:
            st.markdown("""
<div style="background:#F0FDF4;border:1px solid #A7F3D0;border-radius:10px;
     padding:24px;text-align:center">
  <div style="font-size:1.8rem;margin-bottom:10px">📂</div>
  <div style="font-weight:700;color:#005C2E;margin-bottom:6px">
    Carga un portafolio para optimizar</div>
  <div style="font-size:0.78rem;color:#374151">
    Arrastra tu Excel/CSV en el panel lateral.<br>
    El MILP necesita al menos 2 proyectos.
  </div>
</div>""", unsafe_allow_html=True)
        with col_opt_e2:
            st.markdown("""
<div style="background:#EFF6FF;border:1px solid #BFDBFE;border-radius:10px;
     padding:24px;text-align:center">
  <div style="font-size:1.8rem;margin-bottom:10px">🔬</div>
  <div style="font-weight:700;color:#1E40AF;margin-bottom:6px">
    O usa el Wizard para Análisis Local</div>
  <div style="font-size:0.78rem;color:#374151">
    En modo <b>Análisis Local</b> el Wizard crea las alternativas
    y el optimizador las compara sin necesitar un archivo externo.
  </div>
</div>""", unsafe_allow_html=True)
        st.info("El Optimizador estará disponible una vez que se cargue el portafolio.")
        # No st.stop() — no bloquea el resto de la app

    # ══════════════════════════════════════════════════════════════════════════
    # SELECTOR DE ESCENARIO
    # ══════════════════════════════════════════════════════════════════════════
    if not projects:
        pass   # empty state guidance shown above; rest of UI is harmless

    st.markdown("""
<div style="background:#F0FDF4;border:1px solid #A7F3D0;border-radius:8px;
     padding:14px 20px;margin-bottom:16px">
<b style="color:#005C2E;font-size:0.9rem">Selecciona el alcance de optimización:</b>
</div>""", unsafe_allow_html=True)

    esc_c1, esc_c2, esc_c3 = st.columns(3)
    with esc_c1:
        if st.button("🎯 Local — Análisis de Alternativas",
                     use_container_width=True,
                     help="Compara n alternativas para un mismo problema. "
                          "El MILP elige la mejor (o ninguna — Status Quo)."):
            st.session_state["opt_scenario"] = "local"
    with esc_c2:
        if st.button("🏢 Silo de Negocio",
                     use_container_width=True,
                     help="Optimiza solo un negocio (ej. Generación) "
                          "contra su presupuesto propio, sin competir con otras BU."):
            st.session_state["opt_scenario"] = "silo"
    with esc_c3:
        if st.button("🌐 Enterprise Global",
                     use_container_width=True,
                     help="Optimiza todo EPM con pisos de inversión por negocio "
                          "para evitar canibalización presupuestal entre BU."):
            st.session_state["opt_scenario"] = "global"

    scenario = st.session_state.get("opt_scenario", "global")
    SCENARIO_LABELS = {
        "local":  "🎯 Local — Análisis de Alternativas",
        "silo":   "🏢 Silo de Negocio",
        "global": "🌐 Enterprise Global",
    }
    st.markdown(
        f'<div class="box-ok"><b>Escenario activo: {SCENARIO_LABELS[scenario]}</b></div>',
        unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # PARÁMETROS COMUNES
    # ══════════════════════════════════════════════════════════════════════════
    with st.expander("⚙️ Parámetros Base", expanded=True):
        col_p1, col_p2, col_p3 = st.columns(3)
        with col_p1:
            budget_pct = st.slider("Presupuesto disponible (%)", 10, 100, 75, 5,
                help="Porcentaje del CAPEX total 2026-2030 disponible")
        with col_p2:
            max_pm = st.number_input("Capacidad persona-mes/año", 20, 500, 100, 10)
        with col_p3:
            solver_tl = st.number_input("Tiempo límite solver (s)", 30, 600, 120, 30)

        col_p4, col_p5, col_p6 = st.columns(3)
        with col_p4:
            max_conc = st.number_input("Proyectos concurrentes máx.", 5, 50, 20, 1)
        with col_p5:
            compute_fe = st.checkbox(
                "Frontera eficiente", value=True,
                help=("Barre el cupo de 10% a 150% del declarado y traza VNT vs. "
                      "presupuesto, con el valor marginal por tramo y el punto de "
                      "quiebre. Es el tratamiento de la incertidumbre presupuestal: "
                      "responde cuánto pedir, no solo cómo repartir lo que dieron. "
                      "Cuesta ~15 corridas adicionales del MILP."))
        with col_p6:
            cascade_cod = st.checkbox("Cascada CoD en precedencias", value=True)

    # ══════════════════════════════════════════════════════════════════════════
    # PARÁMETROS ESPECÍFICOS DEL ESCENARIO
    # ══════════════════════════════════════════════════════════════════════════
    bu_filter_opt    = None
    pisos_opt        = None
    group_id_local   = None

    if scenario == "local":
        st.markdown("""
<div class="box-ok">🎯 <b>Modo Local:</b> el optimizador selecciona a lo sumo <b>1 alternativa</b>
del grupo de exclusión configurado. Si ninguna tiene VNT marginal positivo, el resultado
es el <b>Status Quo</b> (no se invierte — la inacción es más barata que actuar).</div>""",
        unsafe_allow_html=True)
        # Extraer grupos de exclusión disponibles
        grupos = sorted(set(
            p.exclusion_group_id for p in projects
            if getattr(p, "exclusion_group_id", None)
        ))
        if grupos:
            group_id_local = st.selectbox(
                "Grupo de alternativas a comparar (exclusion_group_id)",
                grupos,
                help="Solo se evalúan los proyectos de este grupo")
        else:
            st.warning("No hay grupos de exclusión (exclusion_group_id) en el portafolio. "
                       "Usa el Wizard de Proyecto para crear alternativas con grupo de exclusión.")

    elif scenario == "silo":
        st.markdown("""
<div class="box-ok">🏢 <b>Modo Silo:</b> solo los proyectos del negocio seleccionado
compiten por el presupuesto del silo. Cada BU se optimiza de forma independiente.</div>""",
        unsafe_allow_html=True)
        bus_disponibles = sorted(set(p.business_unit for p in projects))
        bu_filter_opt = st.selectbox(
            "Negocio a optimizar",
            bus_disponibles,
            format_func=lambda bu: BU_DRIVER_CONFIG.get(bu, {}).get("label", bu))
        n_silo = sum(1 for p in projects if p.business_unit == bu_filter_opt)
        st.caption(f"{n_silo} proyectos de {bu_filter_opt} en el portafolio")

    else:  # global
        st.markdown("""
<div class="box-ok">🌐 <b>Modo Enterprise Global:</b> todos los proyectos de EPM compiten
por el presupuesto corporativo. Los <b>pisos de inversión</b> garantizan financiación mínima
por negocio para evitar que el optimizer canibalice las BU de menor VEI relativo.</div>""",
        unsafe_allow_html=True)

        with st.expander("🔒 Pisos de Inversión por Negocio (restricción de equidad)", expanded=True):
            st.caption(
                "Define el CAPEX mínimo que EPM debe asignar a cada negocio en todo el "
                "horizonte 2026-2030. Deja en 0 para dejar esa BU sin restricción.")
            total_cap_all = sum(p.costs.capex_COP for p in projects)

            piso_cols = st.columns(5)
            bu_labels = {
                "TyD": "T&D", "Generacion": "Generación",
                "AyA": "AyA", "Gas": "Gas", "Todos": "TI/Otros"
            }
            pisos_opt = {}
            bus_in_port = sorted(set(p.business_unit for p in projects))
            piso_col_map = {bu: piso_cols[i % 5] for i, bu in enumerate(bus_in_port)}
            for bu in bus_in_port:
                cap_bu = sum(p.costs.capex_COP for p in projects if p.business_unit == bu)
                with piso_col_map[bu]:
                    piso_pct = st.number_input(
                        f"{bu_labels.get(bu, bu)} (% del portafolio)",
                        min_value=0.0, max_value=50.0,
                        value=0.0, step=1.0,
                        key=f"piso_{bu}",
                        help=f"CAPEX {bu}: ${cap_bu/1e9:.2f}B en portafolio")
                    pisos_opt[bu] = cap_bu * (piso_pct / 100)

            pisos_activos = {bu: v for bu, v in pisos_opt.items() if v > 0}
            if pisos_activos:
                st.caption("Pisos activos: " +
                           " · ".join(f"{bu_labels.get(bu,bu)}: ${v/1e9:.2f}B"
                                      for bu, v in pisos_activos.items()))
            else:
                pisos_opt = None  # Sin pisos → optimización libre

    # ── Presupuesto por vigencia ──────────────────────────────────────────────
    section_bar("📅 Presupuesto por Vigencia")
    years_cfg = [2026, 2027, 2028, 2029, 2030]

    # Calcular CAPEX base según escenario
    projects_for_budget = (
        [p for p in projects if p.exclusion_group_id == group_id_local]
        if scenario == "local" and group_id_local else
        [p for p in projects if p.business_unit == bu_filter_opt]
        if scenario == "silo" and bu_filter_opt else
        projects
    )
    capex_total_by_yr: Dict[int, float] = {}
    for yr in years_cfg:
        for p in projects_for_budget:
            by_yr = getattr(p.costs, 'capex_by_year', {}) or {}
            capex_total_by_yr[yr] = capex_total_by_yr.get(yr, 0) + by_yr.get(yr, 0)
    if sum(capex_total_by_yr.values()) == 0:
        total_cap_b = sum(p.costs.capex_COP for p in projects_for_budget)
        for yr in years_cfg:
            capex_total_by_yr[yr] = total_cap_b / len(years_cfg)

    cols_yr = st.columns(len(years_cfg))
    budget_by_year: Dict[int, float] = {}
    for i, yr in enumerate(years_cfg):
        base = capex_total_by_yr.get(yr, 0)
        budget_by_year[yr] = base * (budget_pct / 100)
        with cols_yr[i]:
            st.metric(str(yr), f"${budget_by_year[yr]/1e9:.2f}B",
                      f"de ${base/1e9:.2f}B ({budget_pct}%)")

    # ── Brecha I1: Restricción TOTEX (opcional) ───────────────────────────────
    with st.expander("⚖️ Restricción TOTEX por Vigencia (Brecha I1)", expanded=False):
        st.caption(
            "Define un techo **TOTEX** (CAPEX + NPV OPEX incremental) por vigencia. "
            "El OPEX lo ingresa el analista en `opex_incremental_COP/año` de cada proyecto. "
            "El Factor NPV se calcula con WACC=10%, Horizonte=10 años (factor ≈ 6.14). "
            "Deja en 0 para no aplicar restricción TOTEX en esa vigencia."
        )
        _totex_cols = st.columns(len(years_cfg))
        _totex_by_year: Dict[int, float] = {}
        for _ti, _yr_t in enumerate(years_cfg):
            with _totex_cols[_ti]:
                _tb_val = st.number_input(
                    f"TOTEX {_yr_t} (B COP)",
                    min_value=0.0, max_value=50.0, value=0.0, step=0.1,
                    format="%.1f",
                    key=f"totex_budget_{_yr_t}",
                    help=f"Techo TOTEX para {_yr_t} en B COP. 0 = sin restricción."
                )
                _totex_by_year[_yr_t] = _tb_val * 1e9 if _tb_val > 0 else None

    periods = [
        ResourcePeriod(
            year=yr,
            budget_COP=budget_by_year[yr],
            max_person_months=float(max_pm),
            max_projects_concurrent=int(max_conc),
            totex_budget_COP=_totex_by_year.get(yr),
        )
        for yr in years_cfg
    ]

    # ── Brecha H1: Información de proyectos comprometidos ─────────────────────
    _n_committed_pre = sum(1 for p in projects if getattr(p, "is_committed", False))
    if _n_committed_pre > 0:
        _cap_committed_pre = sum(
            p.costs.capex_COP for p in projects if getattr(p, "is_committed", False)
        )
        st.info(
            f"📋 **{_n_committed_pre} proyecto(s) comprometido(s)** detectados "
            f"(CAPEX: {fmt_cop(_cap_committed_pre)}). "
            "El MILP los fijará en su año de compromiso (R11).",
            icon="📋",
        )

    st.divider()

    # ── Ejecutar ──────────────────────────────────────────────────────────────
    if not PULP_OK:
        st.error("❌ PuLP no instalado. Ejecuta: `pip install pulp`")
    else:
        run_col, status_col = st.columns([1, 3])
        with run_col:
            run_opt = st.button("▶ EJECUTAR OPTIMIZACIÓN",
                                type="primary", use_container_width=True)
        with status_col:
            if st.session_state.get("opt_result"):
                r = st.session_state["opt_result"]
                st.success(
                    f"✅ {SCENARIO_LABELS.get(st.session_state.get('opt_scenario_run',''), '')} · "
                    f"{r.solver_status} · {len(r.selected)} proyectos · "
                    f"VNT {fmt_cop(r.total_net_value)} · {r.solve_time_s:.1f}s"
                )

        if run_opt:
            # Determinar proyectos a optimizar según escenario
            if scenario == "local" and group_id_local:
                projs_opt = [p for p in projects
                             if getattr(p, "exclusion_group_id", None) == group_id_local]
            elif scenario == "silo" and bu_filter_opt:
                projs_opt = [p for p in projects if p.business_unit == bu_filter_opt]
            else:
                projs_opt = projects

            if not projs_opt:
                st.error("No hay proyectos que optimizar con los filtros actuales.")
                st.stop()

            with st.spinner(f"⏳ Ejecutando MILP — {SCENARIO_LABELS[scenario]}..."):
                try:
                    cfg_tmp = copy.deepcopy(cfg)
                    cfg_tmp.setdefault("dependency_engine", {})
                    cfg_tmp["dependency_engine"]["cascade_cod_enabled"] = cascade_cod

                    opt = EnterpriseOptimizer(config=cfg_tmp)

                    dep_eng = DependencyEngine(cfg_tmp)
                    try:
                        warnings_dep = dep_eng.validate(projs_opt)
                        for w in warnings_dep:
                            st.warning(w)
                    except DependencyError as de:
                        st.error(f"⛔ Error de dependencias: {de}")
                        st.stop()

                    # ── Brecha I2: degradation_hints desde resultados del Módulo B ──
                    _deg_results = st.session_state.get("degradation_results", [])
                    _deg_hints_map: dict = {}
                    if _deg_results:
                        for _dr in _deg_results:
                            _aid = getattr(_dr, "asset_id", "")
                            _byr = getattr(_dr, "optimal_intervention_year", None)
                            if _aid and _byr:
                                _deg_hints_map[_aid] = _byr

                    result = opt.optimize(
                        projs_opt, periods,
                        solver_time_limit_s=int(solver_tl),
                        compute_shadow_prices=True,
                        compute_frontier=compute_fe,
                        # v8.4 params
                        bu_filter=bu_filter_opt if scenario == "silo" else None,
                        pisos_inversion_COP=pisos_opt if scenario == "global" else None,
                        # ── Brecha I2: degradation hints ──────────────────────
                        degradation_hints=_deg_hints_map or None,
                    )
                    st.session_state["opt_result"]      = result
                    st.session_state["periods"]         = periods
                    st.session_state["opt_scenario_run"]= scenario
                    st.rerun()
                except Exception as e:
                    st.error(f"Error en el optimizador: {e}")
                    with st.expander("Traceback"):
                        st.code(traceback.format_exc())

    # ── Resultados ────────────────────────────────────────────────────────────
    result: Optional[EnterprisePortfolioResult] = st.session_state.get("opt_result")
    if not result:
        st.info("Configura los parámetros y presiona **▶ Ejecutar Optimización**.")
    else:
        st.divider()
        scenario_run = st.session_state.get("opt_scenario_run", "global")

        # KPIs del resultado
        k1, k2, k3, k4, k5 = st.columns(5)
        with k1: metric_card("Proyectos Seleccionados",
                              str(len(result.selected)),
                              f"{len(result.postponed)} postergados")
        with k2: metric_card("VNT Entregado",
                              fmt_cop(result.total_net_value), "NPV COP")
        with k3: metric_card("CAPEX Asignado",
                              fmt_cop(result.total_capex), "")
        with k4: metric_card("Escenario", SCENARIO_LABELS.get(scenario_run, scenario_run),
                              f"{result.solver_status} · {result.solve_time_s:.1f}s",
                              EPM_TEAL if result.solver_status == "OPTIMAL" else EPM_AMBER)
        with k5:
            dragged = sum(1 for r in result.selected if r.get("dragged_by_precedence"))
            metric_card("Postergados por Precedencia",
                        str(dragged), "Efecto cascada", EPM_AMBER)

        # ── Breakdown por BU (solo en Global/Silo) ────────────────────────────
        if scenario_run in ("global", "silo") and result.selected:
            section_bar("🏢 Distribución de Valor por Negocio")
            bu_agg: Dict[str, dict] = {}
            for r in result.selected:
                bu = r.get("business_unit", "?")
                if bu not in bu_agg:
                    bu_agg[bu] = {"n": 0, "vnt": 0.0, "capex": 0.0}
                bu_agg[bu]["n"]     += 1
                bu_agg[bu]["vnt"]   += r.get("net_value_COP", 0)
                bu_agg[bu]["capex"] += r.get("capex_COP", 0)

            bu_cols = st.columns(max(1, len(bu_agg)))
            for col_bu, (bu, agg) in zip(bu_cols, bu_agg.items()):
                bu_label = BU_DRIVER_CONFIG.get(bu, {}).get("label", bu)
                with col_bu:
                    metric_card(
                        bu_label.split("(")[0].strip(),
                        fmt_cop(agg["vnt"]),
                        f"{agg['n']} proyectos · CAPEX {fmt_cop(agg['capex'])}",
                        EPM_GREEN if agg["vnt"] > 0 else EPM_RED,
                    )

            # Verificar pisos de inversión cumplidos
            if scenario_run == "global" and pisos_opt:
                for bu, piso in pisos_opt.items():
                    if piso > 0:
                        asignado = bu_agg.get(bu, {}).get("capex", 0)
                        if asignado >= piso * 0.98:  # 2% tolerancia numérica
                            st.markdown(
                                f'<div class="box-ok">✅ Piso {bu}: '
                                f'${asignado/1e9:.2f}B ≥ ${piso/1e9:.2f}B mínimo</div>',
                                unsafe_allow_html=True)
                        else:
                            st.markdown(
                                f'<div class="box-warn">⚠️ Piso {bu}: '
                                f'${asignado/1e9:.2f}B asignado vs ${piso/1e9:.2f}B mínimo '
                                f'— presupuesto insuficiente para cumplir el piso</div>',
                                unsafe_allow_html=True)

        # ── Utilización por vigencia ──────────────────────────────────────────
        section_bar("📅 Utilización Presupuestal por Vigencia")
        if result.budget_utilization:
            bu_data = []
            for yr, bu in sorted(result.budget_utilization.items()):
                ru = result.resource_utilization.get(yr, {})
                bu_data.append({
                    "Vigencia": str(yr),
                    "Usado (MM)": bu["used_COP"]/1e6,
                    "Disponible (MM)": bu["available_COP"]/1e6,
                    "% Presupuesto": round(bu["pct"], 1),
                    "P-M usados": round(ru.get("used_pm", 0), 0),
                })
            df_bu = pd.DataFrame(bu_data)

            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(
                x=df_bu["Vigencia"], y=df_bu["Disponible (MM)"],
                name="Presupuesto disponible",
                marker_color=EPM_LIGHT, marker_line_color="#007A3E",
                marker_line_width=1.5,
            ))
            fig_bar.add_trace(go.Bar(
                x=df_bu["Vigencia"], y=df_bu["Usado (MM)"],
                name="CAPEX asignado",
                marker_color="#007A3E",
            ))
            fig_bar.update_layout(
                barmode="overlay", height=300,
                legend=dict(orientation="h", y=-0.2),
                yaxis_title="MM COP",
                margin=dict(t=20, b=40),
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_bar, use_container_width=True)

            # Alerta si alguna vigencia excede
            for yr, bu in result.budget_utilization.items():
                if bu["pct"] > 100.5:
                    st.markdown(
                        f'<div class="box-err">⚠️ Vigencia {yr}: '
                        f'utilización {bu["pct"]:.1f}% — excede el cupo</div>',
                        unsafe_allow_html=True
                    )

        # ══════════════════════════════════════════════════════════════════════
        # MÓDULO 3 — CAPACITY GAP: Brecha de Capacidad Financiera
        # Mide el gap entre presupuesto disponible y CAPEX comprometido.
        # Monetiza con shadow_price → VNT No Capturado por vigencia.
        # ══════════════════════════════════════════════════════════════════════
        if result.budget_utilization:
            section_bar("💰 Capacity Gap — Brecha de Capacidad Financiera y VNT No Capturado")
            st.caption(
                "El gráfico mide cuánto presupuesto quedó sin asignar en cada vigencia "
                "(Capacidad Financiera No Utilizada) y lo monetiza al shadow price del MILP. "
                "El **VNT No Capturado** revela el costo de oportunidad de no ampliar el cupo."
            )
            _expert_cap = st.session_state.get("expert_mode", False)

            # ── Datos por vigencia ─────────────────────────────────────────────
            _cap_years, _cap_avail, _cap_used, _cap_gap = [], [], [], []
            _cap_overcommit = []   # G10: vigencias con sobre-asignación
            for _yr, _bu in sorted(result.budget_utilization.items()):
                _cap_years.append(str(_yr))
                _avail = _bu["available_COP"] / 1e6
                _used  = _bu["used_COP"] / 1e6
                _raw_gap = _avail - _used
                _cap_avail.append(_avail)
                _cap_used.append(_used)
                _cap_gap.append(max(0.0, _raw_gap))         # gap positivo (cupo libre)
                _cap_overcommit.append(_raw_gap < -0.5)     # G10: over-commit flag (> 0.5 MM tolerancia)

            # G8: shadow price por vigencia usando claves "Presupuesto_{year}"
            # Más preciso que un único SP global — refleja la restricción real de cada año.
            _sp_values = result.shadow_prices if result.shadow_prices else {}
            _max_sp = max((abs(v) for v in _sp_values.values()), default=0.0)  # fallback global
            # Extraer SP presupuestal por año: {"2026": 0.0045, "2027": 0.0091, ...}
            _sp_by_yr = {
                k.replace("Presupuesto_", ""): abs(v)
                for k, v in _sp_values.items()
                if k.startswith("Presupuesto_")
            }
            # VNT No Capturado por vigencia: usa SP del año si existe, sino el global
            _sp_per_yr   = [_sp_by_yr.get(yr, _max_sp) for yr in _cap_years]
            _vnc         = [gap * sp / 1e6 for gap, sp in zip(_cap_gap, _sp_per_yr)]
            _total_gap_mm   = sum(_cap_gap)
            _total_vnc_mm   = sum(_vnc)
            _total_capex_mm = sum(_cap_used)
            _total_avail_mm = sum(_cap_avail)
            _pct_utiliz     = (_total_capex_mm / max(_total_avail_mm, 1)) * 100

            # ── KPI cards (compactos) ─────────────────────────────────────────
            _cg_k1, _cg_k2, _cg_k3, _cg_k4 = st.columns(4, gap="medium")
            with _cg_k1:
                metric_card("Cupo Total Disponible",
                            f"{_total_avail_mm:,.0f} MM",
                            f"{len(_cap_years)} vigencias · MILP Enterprise")
            with _cg_k2:
                metric_card("CAPEX Comprometido",
                            f"{_total_capex_mm:,.0f} MM",
                            f"{_pct_utiliz:.1f}% del cupo")
            with _cg_k3:
                metric_card("Gap Financiero Total",
                            f"{_total_gap_mm:,.0f} MM",
                            f"{100-_pct_utiliz:.1f}% sin utilizar",
                            EPM_AMBER if _total_gap_mm > 0 else EPM_GREEN)
            with _cg_k4:
                metric_card("VNT No Capturado",
                            f"{_total_vnc_mm:,.1f} MM",
                            f"SP máx {_max_sp*1e6:.0f} MM/MM",
                            EPM_RED if _total_vnc_mm > 50 else EPM_AMBER)

            # ── Gráfico principal: Demand vs Supply + Gap ──────────────────────
            _fig_cap = go.Figure()

            # Área de presupuesto disponible (Demand = cupo = oferta de capital)
            _fig_cap.add_trace(go.Bar(
                x=_cap_years, y=_cap_avail,
                name="Cupo Disponible (Presupuesto)",
                marker_color=EPM_LIGHT,
                marker_line_color=EPM_DARK,
                marker_line_width=1.5,
            ))
            # Área CAPEX comprometido (Supply de proyectos aprobados)
            _fig_cap.add_trace(go.Bar(
                x=_cap_years, y=_cap_used,
                name="CAPEX Comprometido (Proyectos MILP)",
                marker_color=EPM_GREEN,
            ))
            # Línea VNT No Capturado (eje secundario)
            if _max_sp > 0 and any(v > 0 for v in _vnc):
                _fig_cap.add_trace(go.Scatter(
                    x=_cap_years, y=_vnc,
                    mode="lines+markers+text",
                    name="VNT No Capturado (eje der.)",
                    line=dict(color=EPM_RED, width=2.5, dash="dot"),
                    marker=dict(size=9, color=EPM_RED, symbol="diamond"),
                    text=[f"{v:,.0f}MM" if v > 0.5 else "" for v in _vnc],
                    textposition="top center",
                    textfont=dict(size=9, color=EPM_RED),
                    yaxis="y2",
                ))

            # Anotaciones de gap > 0
            for _i, (_yr, _gap, _vnc_i) in enumerate(
                zip(_cap_years, _cap_gap, _vnc)
            ):
                if _gap > 0.5:
                    _fig_cap.add_annotation(
                        x=_yr, y=_cap_avail[_i],
                        text=f"Gap {_gap:,.0f}MM",
                        showarrow=False,
                        font=dict(size=9, color=EPM_AMBER),
                        yshift=12,
                    )

            # ── Brecha L: overlay bandas de outage en el gráfico CAPEX ──────
            _oa_overlay = st.session_state.get("planned_outages", [])
            _oa_overlay_years = sorted(set(o.year for o in _oa_overlay
                                           if o.year in _cap_years))
            _oa_type_colors_light = {
                "maintenance": "rgba(59,130,246,0.10)",
                "forced":      "rgba(239,68,68,0.10)",
                "regulatory":  "rgba(168,85,247,0.10)",
            }
            for _oa_yr in _oa_overlay_years:
                _oa_types_yr = {o.outage_type for o in _oa_overlay if o.year == _oa_yr}
                _oa_names_yr = "; ".join(
                    o.name for o in _oa_overlay if o.year == _oa_yr
                )[:60]
                _oa_col = _oa_type_colors_light.get(
                    list(_oa_types_yr)[0] if len(_oa_types_yr) == 1 else "maintenance",
                    "rgba(100,116,139,0.10)"
                )
                _fig_cap.add_vrect(
                    x0=_oa_yr - 0.45, x1=_oa_yr + 0.45,
                    fillcolor=_oa_col, layer="below", line_width=0,
                    annotation_text="🔧",
                    annotation_position="top left",
                    annotation_font=dict(size=10),
                    annotation_hovertext=_oa_names_yr,
                )

            _fig_cap.update_layout(
                barmode="overlay",
                height=360,
                margin=dict(t=20, b=60, l=70, r=80),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(15,23,42,0.6)",
                font=dict(family="Inter,Arial", size=11, color=EPM_SLATE),
                yaxis=dict(
                    title="MM COP",
                    gridcolor="#F1F5F9",
                    tickformat=",.0f",
                ),
                yaxis2=dict(
                    title="VNT No Capturado (MM COP)",
                    overlaying="y",
                    side="right",
                    gridcolor="#FEE2E2",
                    tickformat=",.0f",
                    showgrid=False,
                ) if _max_sp > 0 else {},
                xaxis=dict(title="Vigencia", gridcolor="#F1F5F9"),
                legend=dict(orientation="h", y=-0.22, font=dict(size=10)),
                title=dict(
                    text="Capacidad Financiera: Cupo vs. CAPEX Comprometido"
                    + (" · 🔧 Ventanas outage marcadas" if _oa_overlay_years else ""),
                    font=dict(size=12, color=EPM_DEEP),
                ),
            )
            st.plotly_chart(_fig_cap, use_container_width=True)

            # ── Panel de interpretación ────────────────────────────────────────
            if _total_gap_mm > 0 and _max_sp > 0:
                st.markdown(
                    f'<div style="background:#FEF3C7;border-left:4px solid {EPM_AMBER};'
                    f'border-radius:0 8px 8px 0;padding:10px 16px;font-size:0.78rem;'
                    f'color:{EPM_SLATE};margin-top:8px">'
                    f'📌 <b>Palanca de asignación de capital:</b> El cupo sin utilizar '
                    f'({_total_gap_mm:,.0f} MM COP) podría financiar proyectos adicionales '
                    f'que, al shadow price actual del MILP ({_max_sp:.4f} COP VNT/COP CAPEX), '
                    f'generarían <b style="color:{EPM_RED}">{_total_vnc_mm:,.1f} MM COP</b> '
                    f'de VNT incremental — valor hoy no capturado en el portafolio.'
                    f'</div>',
                    unsafe_allow_html=True)
            elif _total_gap_mm <= 0:
                st.markdown(
                    f'<div class="box-ok">✅ El cupo está plenamente asignado — '
                    f'no existe gap de capacidad financiera en ninguna vigencia.</div>',
                    unsafe_allow_html=True)

            # ── Vista Experta: tabla numérica + shadow price por vigencia ──────
            # G10: advertencia de sobre-asignación si alguna vigencia supera el cupo
            if any(_cap_overcommit):
                _oc_yrs = [yr for yr, oc in zip(_cap_years, _cap_overcommit) if oc]
                st.warning(
                    f"⚠️ **Over-commitment detectado** en vigencia(s) {', '.join(_oc_yrs)}: "
                    f"el CAPEX comprometido supera el cupo disponible. "
                    f"Revisa restricciones de presupuesto en el MILP.",
                    icon="🚨"
                )

            if _expert_cap:
                with st.expander("🔬 Detalle técnico — Gap y VNT No Capturado por vigencia"):
                    # G9: SP individual por vigencia (no el mismo valor repetido)
                    _df_gap = pd.DataFrame({
                        "Vigencia":               _cap_years,
                        "Cupo Disponible (MM)":   [f"{v:,.1f}" for v in _cap_avail],
                        "CAPEX Comprometido (MM)":[f"{v:,.1f}" for v in _cap_used],
                        "Gap (MM)":               [f"{v:,.1f}" for v in _cap_gap],
                        "Shadow Price (COP/COP)": [f"{sp:.4f}" for sp in _sp_per_yr],
                        "VNT No Capturado (MM)":  [f"{v:,.1f}" for v in _vnc],
                        "Over-commit":            ["⚠️ Sí" if oc else "—"
                                                   for oc in _cap_overcommit],
                    })
                    st.dataframe(_df_gap, use_container_width=True, hide_index=True)
                    _sp_note = ("SP por vigencia (Presupuesto_año)"
                                if _sp_by_yr else
                                "SP global (max constraint) — sin desglose por año disponible")
                    st.markdown(
                        f'<div style="background:#0F172A;border:1px solid rgba(0,168,107,0.3);'
                        f'border-radius:8px;padding:12px 18px;font-family:\'JetBrains Mono\','
                        f'monospace;font-size:0.76rem;color:#E2E8F0;line-height:1.9;margin-top:8px">'
                        f'<span style="color:#6EE7B7">VNT_No_Capturado[año]</span>'
                        f' = Gap_Financiero[año] × Shadow_Price_Presupuesto[año]<br>'
                        f'Total = <b style="color:#4ADE80">{_total_vnc_mm:,.1f} MM COP</b><br>'
                        f'<span style="color:#94A3B8;font-size:0.68rem">'
                        f'{_sp_note} · ISO 55001 §6.2</span>'
                        f'</div>',
                        unsafe_allow_html=True)

        # ── Demand Pipeline vs Supply (enriquecido) ──────────────────────────
        # Compara la demanda TOTAL del pipeline (selected + postponed + infeasible)
        # contra la oferta de capital (budget supply), monetizando el exceso de
        # demanda al shadow price del MILP. Responde: "¿cuánto VNT se pierde
        # porque el cupo no alcanza para financiar todo el pipeline viable?"
        _all_pipeline = (list(result.selected or []) +
                         list(result.postponed or []) +
                         list(result.infeasible or []))
        if _all_pipeline and result.budget_utilization:
            # ── Demanda total del pipeline por vigencia ────────────────────
            _demand_by_yr: dict = {}
            _demand_by_bu_yr: dict = {}   # {bu: {yr: capex_MM}}
            for _pr in _all_pipeline:
                # G11: omitir proyectos sin año definido — evitan barra "?" en el gráfico
                if _pr.get("year") is None:
                    continue
                _pr_yr  = str(_pr["year"])
                _pr_cap = _pr.get("capex_COP", 0) / 1e6
                _pr_bu  = _pr.get("business_unit", "General")
                _demand_by_yr[_pr_yr] = _demand_by_yr.get(_pr_yr, 0) + _pr_cap
                if _pr_bu not in _demand_by_bu_yr:
                    _demand_by_bu_yr[_pr_bu] = {}
                _demand_by_bu_yr[_pr_bu][_pr_yr] = (
                    _demand_by_bu_yr[_pr_bu].get(_pr_yr, 0) + _pr_cap
                )

            _all_yrs_d = sorted(set(_demand_by_yr.keys()) | set(_cap_years))
            _supply_d  = [next((_cap_avail[i] for i, y in enumerate(_cap_years) if y == yr), 0.0)
                          for yr in _all_yrs_d]
            _selected_d = [next((_cap_used[i] for i, y in enumerate(_cap_years) if y == yr), 0.0)
                           for yr in _all_yrs_d]
            _demand_d   = [_demand_by_yr.get(yr, 0.0) for yr in _all_yrs_d]
            _excess_d   = [max(0.0, dem - sup) for dem, sup in zip(_demand_d, _supply_d)]
            _exc_vnc    = [e * _max_sp / 1e6 for e in _excess_d]

            _total_demand_mm = sum(_demand_d)
            _total_excess_mm = sum(_excess_d)
            _total_exc_vnc   = sum(_exc_vnc)
            _ratio_demand    = (_total_demand_mm / max(_total_avail_mm, 1)) * 100

            section_bar(
                "📊 Demand vs Supply — Pipeline Total vs Capacidad Financiera"
            )
            st.caption(
                "Demanda = CAPEX total del pipeline (proyectos seleccionados + postergados + "
                "inviables). Supply = cupo presupuestal aprobado. El exceso de demanda monetizado "
                "al shadow price revela el VNT sistémicamente no alcanzable con el presupuesto actual."
            )

            # KPIs del pipeline
            _pd_k1, _pd_k2, _pd_k3, _pd_k4 = st.columns(4, gap="medium")
            with _pd_k1:
                metric_card("Demanda Total Pipeline",
                            f"{_total_demand_mm:,.0f} MM",
                            f"{_ratio_demand:.0f}% del cupo total disponible",
                            EPM_RED if _ratio_demand > 100 else EPM_AMBER)
            with _pd_k2:
                metric_card("Supply (Cupo aprobado)",
                            f"{_total_avail_mm:,.0f} MM",
                            f"{len(_cap_years)} vigencias")
            with _pd_k3:
                metric_card("Exceso de Demanda",
                            f"{_total_excess_mm:,.0f} MM",
                            "No financiable al cupo actual",
                            EPM_RED if _total_excess_mm > 0 else EPM_GREEN)
            with _pd_k4:
                metric_card("VNT No Alcanzable",
                            f"{_total_exc_vnc:,.1f} MM",
                            f"SP máx {_max_sp * 1e6:.0f} MM/MM",
                            EPM_RED if _total_exc_vnc > 0 else EPM_GREEN)

            # Gráfico Demand vs Supply
            _fig_ds = go.Figure()

            # Supply bar (presupuesto)
            _fig_ds.add_trace(go.Bar(
                x=_all_yrs_d, y=_supply_d,
                name="Supply — Cupo Presupuestal",
                marker_color=EPM_LIGHT,
                marker_line_color=EPM_DARK, marker_line_width=1.5,
                hovertemplate="Año %{x}<br>Supply: %{y:,.0f} MM COP<extra>Cupo</extra>",
            ))
            # Selected (CAPEX MILP aprobado)
            _fig_ds.add_trace(go.Bar(
                x=_all_yrs_d, y=_selected_d,
                name="CAPEX aprobado (MILP)",
                marker_color=EPM_GREEN, opacity=0.85,
                hovertemplate="Año %{x}<br>Aprobado: %{y:,.0f} MM<extra>MILP sel.</extra>",
            ))
            # Demanda total del pipeline — línea
            _fig_ds.add_trace(go.Scatter(
                x=_all_yrs_d, y=_demand_d,
                mode="lines+markers",
                line=dict(color=EPM_AMBER, width=2.5),
                marker=dict(size=8, color=EPM_AMBER, symbol="diamond"),
                name="Demanda Total Pipeline",
                hovertemplate="Año %{x}<br>Demanda: %{y:,.0f} MM<extra>Pipeline</extra>",
            ))
            # Exceso de demanda (fill bajo la línea de demanda, encima del supply)
            if any(e > 0 for e in _excess_d):
                _fig_ds.add_trace(go.Scatter(
                    x=_all_yrs_d + _all_yrs_d[::-1],
                    y=_demand_d + _supply_d[::-1],
                    fill="toself",
                    fillcolor="rgba(220,38,38,0.10)",
                    line=dict(width=0),
                    name="Exceso de Demanda (no financiable)",
                    hoverinfo="skip",
                ))
            # VNT no alcanzable (eje secundario)
            if _max_sp > 0 and any(v > 0 for v in _exc_vnc):
                _fig_ds.add_trace(go.Scatter(
                    x=_all_yrs_d, y=_exc_vnc,
                    mode="lines+markers+text",
                    line=dict(color=EPM_RED, width=2.0, dash="dot"),
                    marker=dict(size=7, color=EPM_RED, symbol="circle"),
                    text=[f"{v:,.0f}MM" if v > 1 else "" for v in _exc_vnc],
                    textposition="top center", textfont=dict(size=8, color=EPM_RED),
                    name="VNT No Alcanzable (eje der.)",
                    yaxis="y2",
                    hovertemplate="Año %{x}<br>VNT no alc.: %{y:,.1f} MM<extra>VNT exceso</extra>",
                ))

            _fig_ds.update_layout(
                barmode="overlay",
                height=380,
                margin=dict(t=10, b=60, l=70, r=80),
                xaxis=dict(title="Vigencia", gridcolor="rgba(255,255,255,0.06)"),
                yaxis=dict(title="MM COP", gridcolor="rgba(255,255,255,0.06)",
                           tickformat=",.0f"),
                yaxis2=dict(
                    title="VNT No Alcanzable (MM COP)",
                    overlaying="y", side="right",
                    showgrid=False, tickformat=",.0f",
                    tickfont=dict(color=EPM_RED),
                    title_font=dict(color=EPM_RED),
                ) if _max_sp > 0 else {},
                legend=dict(orientation="h", y=-0.22, font=dict(size=10)),
                title=dict(
                    text="Pipeline Total: Demanda de Capital vs Cupo Disponible",
                    font=dict(size=11),
                ),
            )
            st.plotly_chart(_fig_ds, use_container_width=True)

            # Interpretación del exceso
            if _total_excess_mm > 0:
                st.markdown(
                    f'<div style="background:#FEF3C7;border-left:4px solid {EPM_AMBER};'
                    f'border-radius:0 8px 8px 0;padding:10px 16px;font-size:0.78rem;'
                    f'color:{EPM_SLATE};margin-top:4px">'
                    f'📌 <b>Déficit estructural:</b> La demanda del pipeline supera el cupo '
                    f'en <b>{_total_excess_mm:,.0f} MM COP</b> acumulados. Al shadow price '
                    f'vigente ({_max_sp:.4f} COP VNT/COP CAPEX), esto equivale a '
                    f'<b style="color:{EPM_RED}">{_total_exc_vnc:,.1f} MM COP</b> de VNT '
                    f'que no puede alcanzarse con el presupuesto actual. '
                    f'Considera ampliar el cupo o priorizar los proyectos de mayor VEI.</div>',
                    unsafe_allow_html=True,
                )

            # ── Desglose por BU ────────────────────────────────────────────
            if len(_demand_by_bu_yr) > 1:
                with st.expander("🏢 Desglose de Demanda por Unidad de Negocio", expanded=False):
                    _bus_order = sorted(_demand_by_bu_yr.keys())
                    _fig_bu_d  = go.Figure()
                    _bu_palette = PLOTLY_COLORS[:len(_bus_order)]
                    for _bui, (_bun, _buc) in enumerate(zip(_bus_order, _bu_palette)):
                        _bu_yr_data = _demand_by_bu_yr[_bun]
                        _by_vals    = [_bu_yr_data.get(yr, 0.0) for yr in _all_yrs_d]
                        _fig_bu_d.add_trace(go.Bar(
                            x=_all_yrs_d, y=_by_vals,
                            name=_bun,
                            marker_color=_buc,
                            hovertemplate=f"{_bun}: %{{y:,.0f}} MM (año %{{x}})<extra></extra>",
                        ))
                    # Línea de supply encima
                    _fig_bu_d.add_trace(go.Scatter(
                        x=_all_yrs_d, y=_supply_d,
                        mode="lines",
                        line=dict(color="#FFFFFF", width=2.5, dash="dash"),
                        name="Supply (cupo)",
                        hovertemplate="Supply: %{y:,.0f} MM<extra>Cupo</extra>",
                    ))
                    _fig_bu_d.update_layout(
                        barmode="stack",
                        height=320,
                        margin=dict(t=10, b=50, l=70, r=20),
                        xaxis=dict(title="Vigencia"),
                        yaxis=dict(title="CAPEX pipeline (MM COP)", tickformat=",.0f"),
                        legend=dict(orientation="h", y=-0.25, font=dict(size=10)),
                    )
                    st.plotly_chart(_fig_bu_d, use_container_width=True)

                    # Tabla resumen por BU
                    _bu_summary = []
                    for _bun in _bus_order:
                        _bu_tot_d = sum(_demand_by_bu_yr[_bun].values())
                        _bu_sel   = sum(
                            r.get("capex_COP", 0) / 1e6
                            for r in result.selected
                            if r.get("business_unit", "") == _bun
                        )
                        _bu_summary.append({
                            "BU": _bun,
                            "Demanda Pipeline (MM)": f"{_bu_tot_d:,.0f}",
                            "CAPEX Aprobado MILP (MM)": f"{_bu_sel:,.0f}",
                            "% Atención": f"{(_bu_sel / max(_bu_tot_d, 1) * 100):.1f}%",
                        })
                    st.dataframe(pd.DataFrame(_bu_summary),
                                 use_container_width=True, hide_index=True)

        # ── Shadow prices ─────────────────────────────────────────────────────
        if result.shadow_prices:
            section_bar("💡 Shadow Prices — Valor de Relajar Restricciones")
            sp_sorted = sorted(result.shadow_prices.items(),
                                key=lambda x: abs(x[1]), reverse=True)[:8]
            fig_sp = go.Figure(go.Bar(
                x=[abs(v)/1e6 for _,v in sp_sorted],
                y=[n for n,_ in sp_sorted],
                orientation="h",
                marker_color="#0F766E",
                text=[f"${abs(v)/1e6:.0f}MM" for _,v in sp_sorted],
                textposition="outside",
            ))
            fig_sp.update_layout(
                height=max(200, len(sp_sorted)*40+60),
                xaxis_title="VNT adicional (MM COP) por unidad relajada",
                margin=dict(t=10, b=40, l=300),
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_sp, use_container_width=True)
            st.caption(
                "Shadow price = cuántos COP de VNT adicional se ganan si se "
                "amplía el presupuesto de esa vigencia en 1 COP."
            )

        # ── Frontera eficiente ────────────────────────────────────────────────
        if result.efficient_frontier:
            section_bar("📈 Frontera Eficiente — VNT vs. Presupuesto")
            fe = result.efficient_frontier
            valid_fe = [p for p in fe if "error" not in p]
            if valid_fe:
                fig_fe = go.Figure()
                fig_fe.add_trace(go.Scatter(
                    x=[p["capex_COP"]/1e9 for p in valid_fe],
                    y=[p["vnt_COP"]/1e9 for p in valid_fe],
                    mode="lines+markers",
                    line=dict(color=EPM_GREEN, width=2.5),
                    marker=dict(size=8, color=EPM_DARK),
                    customdata=[[p["budget_pct"]*100, p["n_projects"]]
                                 for p in valid_fe],
                    hovertemplate=(
                        "Presupuesto: %{customdata[0]:.0f}%<br>"
                        "CAPEX: $%{x:.2f}B<br>"
                        "VNT: $%{y:.2f}B<br>"
                        "Proyectos: %{customdata[1]}<extra></extra>"
                    ),
                    name="Frontera eficiente",
                ))
                # Marcar el punto actual
                curr_cap = result.total_capex / 1e9
                curr_vnt = result.total_net_value / 1e9
                fig_fe.add_trace(go.Scatter(
                    x=[curr_cap], y=[curr_vnt],
                    mode="markers", marker=dict(size=14, color=EPM_AMBER,
                                                 symbol="star"),
                    name=f"Escenario actual ({budget_pct}%)",
                    hovertemplate=f"★ Actual: ${curr_cap:.2f}B → ${curr_vnt:.2f}B<extra></extra>",
                ))
                fig_fe.update_layout(
                    height=380,
                    xaxis_title="CAPEX total asignado (Billones COP)",
                    yaxis_title="VNT entregado (Billones COP)",
                    legend=dict(orientation="h", y=-0.2),
                    margin=dict(t=10, b=60),
                    paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_fe, use_container_width=True)

        # ══════════════════════════════════════════════════════════════════════
        # MÓDULO 6 — PUNTO ROJO: Rentabilidad Nula del Portafolio
        # Concepto de Celso (La Sétima / A7, webinar): el punto donde el
        # CAPEX + OPEX acumulados igualan el VNT acumulado entregado.
        # Más allá de ese punto la empresa ya no crea valor neto. Aquí se
        # aproxima distribuyendo el NPV en flujos anuales uniformes.
        # ══════════════════════════════════════════════════════════════════════
        if result.selected:
            section_bar("🔴 Punto Rojo — Rentabilidad Nula del Portafolio")
            st.caption(
                "El **punto rojo** (La Sétima / A7) es el año en que el CAPEX + OPEX "
                "acumulados del portafolio igualan el VNT acumulado entregado. "
                "Más allá de ese año el portafolio deja de crear valor neto. "
                "Se aproxima distribuyendo cada NPV en flujos anuales uniformes "
                "sobre el horizonte de cada proyecto."
            )

            # Reconstruir flujos anuales por proyecto seleccionado
            _pr_years = sorted(set(
                yr for r in result.selected
                for yr in range(
                    int(r.get("year", 2026)),
                    int(r.get("year", 2026)) + max(int(r.get("horizon_years", 10)), 1)
                )
            ))
            if not _pr_years:
                _pr_years = list(range(2026, 2041))

            _pr_capex_ann  = {yr: 0.0 for yr in _pr_years}
            _pr_opex_ann   = {yr: 0.0 for yr in _pr_years}
            _pr_benefit_ann = {yr: 0.0 for yr in _pr_years}

            for _pr in result.selected:
                _pr_start  = int(_pr.get("year", 2026))
                _pr_horz   = max(int(_pr.get("horizon_years",
                                             _pr.get("horizon_asset_years", 10))), 1)
                _pr_capex  = float(_pr.get("capex_COP", 0))
                _pr_opex   = float(_pr.get("opex_incremental_COP_year", 0))
                _pr_vnt    = float(_pr.get("net_value_COP", 0))
                # CAPEX: pago en el año de inicio
                if _pr_start in _pr_capex_ann:
                    _pr_capex_ann[_pr_start] += _pr_capex
                # OPEX incremental: flujo anual sobre el horizonte
                _pr_opex_yr = abs(_pr_opex)
                for yr in range(_pr_start, _pr_start + _pr_horz):
                    if yr in _pr_opex_ann:
                        _pr_opex_ann[yr] += _pr_opex_yr
                # Beneficio (VNT): distribuido uniformemente sobre el horizonte
                # VNT ya es un NPV; aproximamos como annuity = VNT / horizonte
                _pr_ben_yr = max(_pr_vnt, 0) / _pr_horz
                for yr in range(_pr_start, _pr_start + _pr_horz):
                    if yr in _pr_benefit_ann:
                        _pr_benefit_ann[yr] += _pr_ben_yr

            # Curvas acumuladas
            _pr_cum_cost    = []
            _pr_cum_benefit = []
            _pr_cum_c = 0.0
            _pr_cum_b = 0.0
            for yr in _pr_years:
                _pr_cum_c += (_pr_capex_ann[yr] + _pr_opex_ann[yr]) / 1e6
                _pr_cum_b += _pr_benefit_ann[yr] / 1e6
                _pr_cum_cost.append(_pr_cum_c)
                _pr_cum_benefit.append(_pr_cum_b)

            # Detectar el punto rojo (primer cruce costo ≥ beneficio)
            _pr_red_yr = None
            for _pri, yr in enumerate(_pr_years):
                if _pr_cum_cost[_pri] >= _pr_cum_benefit[_pri] and _pri > 0:
                    _pr_red_yr = yr
                    break

            # KPIs
            _pr_k1, _pr_k2, _pr_k3 = st.columns(3, gap="medium")
            with _pr_k1:
                metric_card(
                    "VNT Total Portafolio",
                    f"{result.total_net_value/1e9:.2f}B COP",
                    f"{len(result.selected)} proyectos seleccionados",
                )
            with _pr_k2:
                metric_card(
                    "CAPEX Comprometido",
                    f"{result.total_capex/1e9:.2f}B COP",
                    "Pago de capital del portafolio",
                )
            with _pr_k3:
                if _pr_red_yr:
                    metric_card(
                        "Punto Rojo Estimado",
                        str(_pr_red_yr),
                        "Año donde costo acumulado ≈ beneficio acumulado",
                        EPM_RED,
                    )
                else:
                    metric_card(
                        "Punto Rojo",
                        "No alcanzado",
                        f"El portafolio crea valor en todo el horizonte ({max(_pr_years)})",
                        EPM_GREEN,
                    )

            # Gráfico
            _fig_pr = go.Figure()
            _fig_pr.add_trace(go.Scatter(
                x=_pr_years,
                y=_pr_cum_benefit,
                mode="lines",
                name="VNT acumulado entregado",
                line=dict(color=EPM_GREEN, width=2.5),
                hovertemplate="Año %{x}<br>Beneficio: %{y:,.0f} MM COP<extra></extra>",
                fill="tozeroy",
                fillcolor="rgba(0,122,62,0.08)",
            ))
            _fig_pr.add_trace(go.Scatter(
                x=_pr_years,
                y=_pr_cum_cost,
                mode="lines",
                name="CAPEX + OPEX acumulado",
                line=dict(color=EPM_RED, width=2.5),
                hovertemplate="Año %{x}<br>Costo: %{y:,.0f} MM COP<extra></extra>",
            ))
            if _pr_red_yr:
                _pr_red_idx = _pr_years.index(_pr_red_yr)
                _fig_pr.add_vline(
                    x=int(_pr_red_yr),
                    line_dash="dot",
                    line_color=EPM_RED,
                    annotation_text=f"● Punto Rojo {_pr_red_yr}",
                    annotation_position="top right",
                    annotation_font_color=EPM_RED,
                )
                _fig_pr.add_trace(go.Scatter(
                    x=[_pr_red_yr],
                    y=[_pr_cum_cost[_pr_red_idx]],
                    mode="markers",
                    marker=dict(size=14, color=EPM_RED, symbol="circle"),
                    name="Punto Rojo",
                    hovertemplate=(
                        f"Punto Rojo: {_pr_red_yr}<br>"
                        "Costo ≈ Beneficio: %{y:,.0f} MM COP<extra></extra>"
                    ),
                ))
            _fig_pr.update_layout(
                height=370,
                xaxis_title="Año",
                yaxis_title="MM COP acumulados",
                legend=dict(orientation="h", y=-0.25),
                margin=dict(t=10, b=70),
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(_fig_pr, use_container_width=True)
            if _pr_red_yr:
                st.caption(
                    f"El portafolio seleccionado alcanza rentabilidad nula en **{_pr_red_yr}**. "
                    "Más allá de ese año el CAPEX + OPEX acumulados superan el VNT entregado. "
                    "Ampliar el horizonte de planeación o reducir OPEX incremental desplaza "
                    "el punto rojo hacia la derecha."
                )
            else:
                st.caption(
                    "El portafolio seleccionado crea valor neto en todo el horizonte modelado. "
                    "El VNT acumulado supera el CAPEX + OPEX en cada año."
                )

        # ── Tabla de proyectos seleccionados ──────────────────────────────────
        st.subheader(f"✅ Proyectos Seleccionados ({len(result.selected)})")
        if result.selected:
            rows_sel = []
            for r in sorted(result.selected, key=lambda x: x["net_value_COP"], reverse=True):
                drag = "⛓" if r.get("dragged_by_precedence") else ""
                mand = "★" if r.get("is_mandatory") else ""
                rows_sel.append({
                    "":       drag + mand,
                    "ID":     r["project_id"],
                    "Nombre": r["project_name"][:42],
                    "BU":     r["business_unit"],
                    "Año":    r["year"],
                    "VNT MM": round(r["net_value_COP"]/1e6, 1),
                    "VEI":    round(r.get("vei", r.get("vei_eav", 0)), 2),
                    "CAPEX MM": round(r["capex_COP"]/1e6, 1),
                    "P-M":    round(r.get("person_months",0), 1),
                    "Padre":  r.get("precedent_project_id","") or "",
                })
            df_sel = pd.DataFrame(rows_sel)
            st.dataframe(
                df_sel.style.format({
                    "VNT MM":"{:,.1f}","CAPEX MM":"{:,.1f}",
                    "VEI":"{:.2f}","P-M":"{:.1f}"
                }),
                use_container_width=True, height=460
            )

            # Proyectos arrastrados
            dragged_items = [r for r in result.selected if r.get("drag_reason")]
            if dragged_items:
                with st.expander(f"⛓ {len(dragged_items)} proyecto(s) postergados por precedencia"):
                    for r in dragged_items:
                        st.markdown(
                            f'<div class="box-warn">⛓ <b>{r["project_id"]}</b>: '
                            f'{r["drag_reason"]}</div>',
                            unsafe_allow_html=True
                        )

        # ── Proyectos postergados ─────────────────────────────────────────────
        if result.postponed:
            with st.expander(f"⏸ {len(result.postponed)} proyecto(s) no incluidos"):
                df_post = pd.DataFrame([{
                    "ID":     r["project_id"],
                    "Nombre": r["project_name"][:45],
                    "Razón":  r.get("reason","Sin cupo"),
                } for r in result.postponed])
                st.dataframe(df_post, use_container_width=True)

        # ══════════════════════════════════════════════════════════════════════
        # BRECHA H1 — GASTO COMPROMETIDO (Committed Spend)
        # Proyectos con contrato/ODS emitida: el MILP los fija (R11).
        # Separación visual: presupuesto comprometido vs. discrecional.
        # ══════════════════════════════════════════════════════════════════════
        st.divider()
        section_bar("📋 Gasto Comprometido vs. Discrecional — Brecha H1")
        _cs = getattr(result, "committed_summary", {}) or {}
        _n_comm = _cs.get("n_committed", 0)
        if _n_comm == 0:
            st.info(
                "ℹ️ No hay proyectos con **`is_committed=True`** en este portafolio. "
                "Para marcar proyectos con contrato/ODS firmada, activa el flag "
                "`Comprometido` en el editor de proyectos o agrega columna "
                "`comprometido` (S/N) en el Excel de carga.",
                icon="📋",
            )
        else:
            _cap_comm = _cs.get("capex_committed_COP", 0.0)
            _cap_disc = _cs.get("capex_discretionary_COP", 0.0)
            _pct_comm = _cs.get("pct_committed", 0.0)
            _h1c1, _h1c2, _h1c3, _h1c4 = st.columns(4)
            with _h1c1:
                metric_card("Proyectos Comprometidos", str(_n_comm),
                            "Contrato / ODS emitida")
            with _h1c2:
                metric_card("CAPEX Comprometido", fmt_cop(_cap_comm),
                            f"{_pct_comm:.1f}% del total")
            with _h1c3:
                metric_card("CAPEX Discrecional", fmt_cop(_cap_disc),
                            "Asignado por el MILP")
            with _h1c4:
                _bar_comm = "█" * int(_pct_comm / 10) + "░" * (10 - int(_pct_comm / 10))
                metric_card("Mix Comprometido/Discrecional",
                            f"{_pct_comm:.0f}% / {100-_pct_comm:.0f}%",
                            _bar_comm)

            # Tabla de proyectos comprometidos
            _comm_projs = _cs.get("committed_projects", [])
            if _comm_projs:
                with st.expander(f"📋 Detalle de {_n_comm} proyecto(s) comprometido(s)",
                                 expanded=False):
                    st.dataframe(
                        pd.DataFrame([{
                            "ID":               r.get("project_id",""),
                            "Nombre":           r.get("project_name","")[:45],
                            "Negocio":          r.get("business_unit",""),
                            "Año Comprometido": r.get("committed_year") or r.get("year",""),
                            "Año Asignado":     r.get("year",""),
                            "CAPEX (MM COP)":   round(r.get("capex_COP",0)/1e6, 1),
                        } for r in _comm_projs]),
                        use_container_width=True, hide_index=True,
                    )
            st.caption(
                "💡 El **gasto comprometido** es irrevocable (R11 fija x=1 en el año del contrato). "
                "El MILP optimiza únicamente el cupo **discrecional** restante. "
                "Si el comprometido supera el presupuesto disponible, el solver reportará infactibilidad."
            )

        # ══════════════════════════════════════════════════════════════════════
        # BRECHA I1/I3 — TOTEX: Utilización + Frontera Eficiente
        # I1: Restricción TOTEX por vigencia (CAPEX + NPV OPEX).
        # I3: Frontera eficiente VNT vs. techo TOTEX.
        # Los datos de OPEX son ingresados por el analista en opex_incremental.
        # ══════════════════════════════════════════════════════════════════════
        st.divider()
        section_bar("⚖️ Análisis TOTEX — Brecha I (CAPEX + OPEX Incremental)")
        st.caption(
            "**TOTEX** = CAPEX + NPV(OPEX incremental). Mide el costo total de propiedad "
            "del portafolio, no solo la inversión inicial. El OPEX lo ingresa el analista "
            "en `opex_incremental_COP/año` de cada proyecto — no requiere conexión con SIGEAS."
        )
        _totex_util = getattr(result, "totex_utilization", {}) or {}
        _deg_hints_applied = getattr(result, "degradation_hints_applied", []) or []

        if not _totex_util:
            st.info(
                "ℹ️ Para ver la utilización TOTEX, completa el campo **OPEX Incremental (COP/año)** "
                "en los proyectos y ejecuta el optimizador.",
                icon="⚖️",
            )
        else:
            # Tabla TOTEX por vigencia
            _totex_rows = []
            for yr, td in sorted(_totex_util.items()):
                _totex_rows.append({
                    "Vigencia":       yr,
                    "CAPEX (MM)":     round(td.get("capex_COP", 0) / 1e6, 0),
                    "OPEX NPV (MM)":  round(td.get("opex_npv_COP", 0) / 1e6, 0),
                    "TOTEX (MM)":     round(td.get("totex_COP", 0) / 1e6, 0),
                    "Techo TOTEX":    (fmt_cop(td["budget_COP"])
                                       if td.get("budget_COP") else "Sin límite"),
                    "Utiliz.%":       (f"{td['pct']:.1f}%"
                                       if td.get("pct") is not None else "—"),
                })
            st.dataframe(
                pd.DataFrame(_totex_rows),
                use_container_width=True, hide_index=True,
            )

        # ── Brecha I2: Proyectos con bono de timing por degradación ───────────
        if _deg_hints_applied:
            with st.expander(
                f"🔧 Brecha I2 — Timing influenciado por DegradationEngine "
                f"({len(_deg_hints_applied)} proyectos)",
                expanded=False,
            ):
                st.caption(
                    "El año óptimo de intervención del Motor de Degradación se usó "
                    "como **preferencia suave** (+5% VNT) en el MILP. "
                    "El solver puede haberlo ignorado si otra vigencia era mejor."
                )
                st.dataframe(
                    pd.DataFrame([{
                        "Proyecto":       r.get("project_id",""),
                        "Activo":         r.get("asset_id",""),
                        "Año Hint":       r.get("hint_year",""),
                        "Año Horizonte":  r.get("horizon_year",""),
                        "Bono COP":       fmt_cop(r.get("bonus_COP", 0)),
                        "Bono %":         f"{r.get('bonus_pct',0):.0f}%",
                    } for r in _deg_hints_applied]),
                    use_container_width=True, hide_index=True,
                )

        # ── Brecha I3: Frontera eficiente TOTEX (bajo demanda) ────────────────
        st.markdown("##### 📈 Frontera Eficiente TOTEX")
        _totex_frontier = getattr(result, "totex_frontier", []) or []
        _hi3_c1, _hi3_c2 = st.columns([3, 1])
        with _hi3_c2:
            _run_totex_frontier = st.button(
                "📈 Calcular Frontera TOTEX",
                help="Corre el MILP 8 veces barriendo el techo TOTEX [50%–150%]. "
                     "Muestra cuánto VNT adicional aporta ampliar el límite TOTEX.",
                use_container_width=True,
                key="btn_totex_frontier",
            )
        if _run_totex_frontier:
            _projects_tf = st.session_state.get("portfolio", [])
            _periods_tf  = st.session_state.get("periods", [])
            if _projects_tf and _periods_tf:
                with st.spinner("Calculando frontera TOTEX (8 puntos)…"):
                    try:
                        _opt_tf = EnterpriseOptimizer(config)
                        _tf_list = _opt_tf._compute_totex_frontier(_projects_tf, _periods_tf)
                        st.session_state["totex_frontier"] = _tf_list
                    except Exception as _e_tf:
                        st.error(f"Error calculando frontera TOTEX: {_e_tf}")
            else:
                st.warning("Carga el portafolio y define las vigencias primero.")

        _totex_frontier = (st.session_state.get("totex_frontier")
                           or getattr(result, "totex_frontier", []) or [])
        if _totex_frontier:
            _tf_valid = [r for r in _totex_frontier if "error" not in r]
            if _tf_valid:
                import plotly.graph_objects as go
                _fig_tf = go.Figure()
                _fig_tf.add_trace(go.Scatter(
                    x=[r["totex_real_COP"] / 1e9 for r in _tf_valid],
                    y=[r["vnt_COP"] / 1e9 for r in _tf_valid],
                    mode="lines+markers+text",
                    text=[f"{r['totex_pct']*100:.0f}%" for r in _tf_valid],
                    textposition="top center",
                    name="Frontera TOTEX",
                    line=dict(color=EPM_GREEN, width=2),
                    marker=dict(size=9, color=EPM_GREEN),
                    hovertemplate=(
                        "<b>TOTEX %{x:.1f}B COP</b><br>"
                        "VNT: %{y:.2f}B COP<br>"
                        "Proyectos: %{customdata[0]}<br>"
                        "CAPEX: %{customdata[1]:.1f}B | OPEX NPV: %{customdata[2]:.1f}B"
                        "<extra></extra>"
                    ),
                    customdata=[[r["n_projects"],
                                 r.get("capex_COP",0)/1e9,
                                 r.get("opex_npv_COP",0)/1e9] for r in _tf_valid],
                ))
                _fig_tf.update_layout(
                    height=350,
                    xaxis_title="TOTEX Real Portafolio (B COP)",
                    yaxis_title="VNT Total (B COP)",
                    title="Frontera Eficiente VNT vs. TOTEX",
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    font_color=EPM_SLATE,
                )
                with _hi3_c1:
                    st.plotly_chart(_fig_tf, use_container_width=True)
        else:
            with _hi3_c1:
                st.info("Haz clic en **Calcular Frontera TOTEX** para ver la curva.", icon="📈")

        # ══════════════════════════════════════════════════════════════════════
        # BRECHA H2a — BORRADOR DE PLAN (save_draft)
        # Guarda propuestas preliminares antes de ejecutar el MILP.
        # ══════════════════════════════════════════════════════════════════════
        st.divider()
        section_bar("📝 Guardar Borrador de Plan — Antes del Optimizador")
        st.caption(
            "Guarda la lista actual de proyectos como borrador PAI sin necesidad de "
            "ejecutar el optimizador. Útil para versionar propuestas preliminares "
            "o compartir el portafolio en revisión. El borrador queda etiquetado "
            "como **DRAFT** y no incluye VNT optimizado."
        )
        _draft_projects = st.session_state.get("portfolio", [])
        if not _draft_projects:
            st.info("Carga el portafolio en la sección superior para habilitar el guardado de borrador.", icon="📂")
        else:
            _dr_c1, _dr_c2 = st.columns([2, 1])
            with _dr_c1:
                _dr_cycle = st.text_input(
                    "Nombre del Borrador",
                    value=f"Borrador PAI {datetime.now().year}-{datetime.now().year+4}",
                    placeholder="Ej: Borrador inicial · Pre-revisión comité",
                    key="draft_cycle_name",
                )
                _dr_author = st.text_input(
                    "Autor",
                    value=st.session_state.get("current_user", {}).get("name", "Analista ASPD"),
                    key="draft_author",
                )
                _dr_desc = st.text_area(
                    "Notas",
                    placeholder="Contexto del borrador, proyectos priorizados, pendientes…",
                    height=60,
                    key="draft_description",
                )
            with _dr_c2:
                st.markdown(
                    f'<div class="box-info" style="margin-top:8px">'
                    f'<b>{len(_draft_projects)}</b> proyectos en portafolio<br>'
                    f'CAPEX total: <b>{sum(p.costs.capex_COP for p in _draft_projects)/1e9:.1f} B COP</b>'
                    f'</div>', unsafe_allow_html=True)
                st.markdown("&nbsp;")
                if st.button("📝 Guardar Borrador", use_container_width=True,
                             key="btn_save_draft",
                             help="Guarda la lista actual como DRAFT en el historial de versiones"):
                    if not _dr_cycle.strip():
                        st.error("Ingresa el nombre del borrador.")
                    else:
                        try:
                            if "plan_version_store" not in st.session_state:
                                _pv_path_dr = str(Path(__file__).parent / "plan_versions.json")
                                st.session_state["plan_version_store"] = PlanVersionStore(_pv_path_dr)
                            _pv_store_dr: PlanVersionStore = st.session_state["plan_version_store"]
                            _draft_saved = _pv_store_dr.save_draft(
                                projects=_draft_projects,
                                cycle_name=_dr_cycle.strip(),
                                author=_dr_author.strip() or "Analista ASPD",
                                description=_dr_desc.strip(),
                            )
                            st.success(
                                f"✅ Borrador **{_draft_saved.cycle_name}** guardado. "
                                f"ID: `{_draft_saved.version_id[:8]}…` | "
                                f"{_draft_saved.n_projects} proyectos | "
                                f"CAPEX: {_draft_saved.total_capex_COP/1e9:.1f} B COP"
                            )
                        except Exception as _e_dr:
                            st.error(f"Error guardando borrador: {_e_dr}")

        # ══════════════════════════════════════════════════════════════════════
        # BRECHA H2 — VERSIONADO DE PLAN PAI (Plan Versioning)
        # Guarda snapshots del plan aprobado por ciclo PAI.
        # ══════════════════════════════════════════════════════════════════════
        st.divider()
        section_bar("📚 Versionado de Plan PAI — Brecha H2")
        st.caption(
            "Guarda el plan actual como versión PAI y compara con versiones anteriores. "
            "Implementa el historial de planes de Copperleaf C55 §5 (Approve & Execute). "
            "Los snapshots son autocontenidos — sin integración ERP/EAM."
        )

        # ── Inicializar store en session_state ────────────────────────────────
        if "plan_version_store" not in st.session_state:
            _pv_path = str(Path(__file__).parent / "plan_versions.json")
            st.session_state["plan_version_store"] = PlanVersionStore(_pv_path)
        _pv_store: PlanVersionStore = st.session_state["plan_version_store"]

        _pv_c1, _pv_c2 = st.columns([2, 1])
        with _pv_c1:
            _pv_cycle = st.text_input(
                "Nombre del Ciclo PAI",
                value=f"PAI {datetime.now().year}-{datetime.now().year+4}",
                placeholder="Ej: PAI 2026-2030 · Revisión Q2",
                key="pv_cycle_name",
            )
            _pv_author = st.text_input(
                "Autor (Analista / Coordinador GA)",
                value="Analista ASPD",
                key="pv_author",
            )
            _pv_desc = st.text_area(
                "Notas del Ciclo",
                placeholder="Cambios vs. versión anterior, restricciones nuevas, supuestos actualizados…",
                height=80,
                key="pv_description",
            )
        with _pv_c2:
            st.markdown("&nbsp;")
            _btn_save_pv = st.button(
                "💾 Guardar Versión",
                type="primary",
                use_container_width=True,
                key="btn_save_plan_version",
                help="Guarda un snapshot firmado (SHA-256) del plan actual.",
            )
            if _btn_save_pv:
                if not _pv_cycle.strip():
                    st.error("Ingresa el nombre del ciclo PAI.")
                else:
                    try:
                        _pv_saved = _pv_store.save_version(
                            result=result,
                            cycle_name=_pv_cycle.strip(),
                            author=_pv_author.strip() or "Analista ASPD",
                            description=_pv_desc.strip(),
                        )
                        st.success(
                            f"✅ Versión **{_pv_saved.cycle_name}** guardada. "
                            f"ID: `{_pv_saved.version_id[:8]}…` | "
                            f"SHA-256: `{_pv_saved.signature[:16]}…`"
                        )
                    except Exception as _e_pv:
                        st.error(f"Error guardando versión: {_e_pv}")

        # ── Historial de versiones ────────────────────────────────────────────
        _pv_list = _pv_store.list_versions()
        if _pv_list:
            st.markdown("##### 📜 Historial de Versiones")
            _pv_tbl = pd.DataFrame([{
                "Versión":           v.cycle_name,
                "Fecha":             v.created_at[:19].replace("T"," "),
                "Autor":             v.author,
                "Proyectos":         v.n_projects,
                "VNT (B COP)":       round(v.total_vnt_COP / 1e9, 2),
                "CAPEX (B COP)":     round(v.total_capex_COP / 1e9, 2),
                "Comprometidos":     v.n_committed,
                "Status Solver":     v.solver_status,
                "ID":                v.version_id[:8] + "…",
            } for v in _pv_list])
            st.dataframe(_pv_tbl, use_container_width=True, hide_index=True)

            # ── Comparación entre versiones ───────────────────────────────────
            if len(_pv_list) >= 2:
                st.markdown("##### 🔀 Comparar Dos Versiones")
                _pv_opts = {f"{v.cycle_name} ({v.created_at[:10]})": v.version_id
                            for v in _pv_list}
                _pv_diff_c1, _pv_diff_c2 = st.columns(2)
                with _pv_diff_c1:
                    _sel_a = st.selectbox("Versión A (base)",
                                          list(_pv_opts.keys()), key="pv_diff_a")
                with _pv_diff_c2:
                    _sel_b = st.selectbox("Versión B (nueva)",
                                          list(_pv_opts.keys()),
                                          index=min(1, len(_pv_opts)-1),
                                          key="pv_diff_b")
                if st.button("🔍 Comparar", key="btn_pv_diff"):
                    _diff = _pv_store.diff_versions(
                        _pv_opts[_sel_a], _pv_opts[_sel_b])
                    if "error" in _diff:
                        st.error(_diff["error"])
                    else:
                        _d1, _d2, _d3, _d4 = st.columns(4)
                        with _d1:
                            metric_card("Proyectos Añadidos",
                                        str(len(_diff.get("added", []))), "en B vs A")
                        with _d2:
                            metric_card("Proyectos Eliminados",
                                        str(len(_diff.get("removed", []))), "en A → no en B")
                        with _d3:
                            metric_card("Cambios de Año",
                                        str(len(_diff.get("year_changes", []))),
                                        "timing modificado")
                        with _d4:
                            _dvnt = _diff.get("vnt_delta_COP", 0)
                            metric_card("ΔVNT",
                                        f"{'+' if _dvnt >= 0 else ''}{_dvnt/1e9:.2f}B",
                                        "B → A")
                        if _diff.get("added"):
                            st.markdown("**✅ Proyectos añadidos en B:**")
                            st.dataframe(pd.DataFrame([{
                                "ID": r.get("project_id",""),
                                "Nombre": r.get("project_name","")[:40],
                                "Año": r.get("year",""),
                                "VNT (MM)": round(r.get("net_value_COP",0)/1e6,1),
                            } for r in _diff["added"]]),
                            use_container_width=True, hide_index=True)
                        if _diff.get("removed"):
                            st.markdown("**❌ Proyectos eliminados de B:**")
                            st.dataframe(pd.DataFrame([{
                                "ID": r.get("project_id",""),
                                "Nombre": r.get("project_name","")[:40],
                                "Año": r.get("year",""),
                                "VNT (MM)": round(r.get("net_value_COP",0)/1e6,1),
                            } for r in _diff["removed"]]),
                            use_container_width=True, hide_index=True)
                        if _diff.get("year_changes"):
                            st.markdown("**📅 Cambios de año de ejecución:**")
                            st.dataframe(pd.DataFrame([{
                                "ID": r.get("project_id",""),
                                "Nombre": r.get("project_name","")[:40],
                                "Año A": r.get("year_a",""),
                                "Año B": r.get("year_b",""),
                            } for r in _diff["year_changes"]]),
                            use_container_width=True, hide_index=True)
        else:
            st.info("Aún no hay versiones guardadas. "
                    "Ejecuta el optimizador y guarda el plan con el botón **Guardar Versión**.",
                    icon="📚")

        # ══════════════════════════════════════════════════════════════════════
        # ROBUSTEZ ANTE LA INCERTIDUMBRE DE ESTIMACIÓN DEL CAPEX
        # Cada proyecto se mueve dentro de SU PROPIA banda, la que declara su
        # base de estimativo. No se escalan beneficios ni riesgo: la
        # incertidumbre de condición del activo ya la trata el Weibull.
        # ══════════════════════════════════════════════════════════════════════
        st.divider()
        section_bar("🎯 Robustez ante la Incertidumbre de Estimación del CAPEX")
        st.caption(
            "Corre el MILP con cada proyecto en los extremos de **su propia** banda de "
            "estimación — la que declara su base de estimativo. Un proyecto con oferta "
            "en firme se mueve ±5-10%; uno en analogía se mueve −30/+50%. Esa diferencia "
            "es la señal: quién entra al portafolio por mérito y quién por tener un "
            "estimativo que nadie ha apretado."
        )

        _cfg_unc_ui  = get_config().get("cost_estimate_uncertainty", {})
        _bands_ui    = _cfg_unc_ui.get("classes", {})
        _default_cls = _cfg_unc_ui.get("default_class", "Class5")

        _sk_c1, _sk_c2 = st.columns([3, 1], gap="medium")
        with _sk_c1:
            if not _cfg_unc_ui.get("_calibrado_con_datos_epm", False):
                st.info(
                    "Las bandas en uso son **referencia de industria** (AACE RP 18R-97), "
                    "no calibración EPM. Se reemplazan por las bandas propias a medida que "
                    "el módulo EVM acumule desviaciones reales de estimativo contra costo "
                    "final. Ajustables en la pestaña **Config**.",
                    icon="📐",
                )
        with _sk_c2:
            _sk_topn = st.number_input(
                "Top-N a mostrar", min_value=3, max_value=50, value=10, step=1,
                key="sk_topn",
                help="Solo afecta cuántas filas se muestran. La clasificación cubre todos los proyectos.",
            )

        _run_sensitivity = st.button(
            "🎯 Evaluar Robustez (3 corridas MILP)",
            type="secondary",
            key="btn_sensitivity_k",
            help="Corre el MILP con costos bajos, base y costos altos. Tarda ~3× una corrida normal.",
        )

        if _run_sensitivity:
            _projs_sens = st.session_state.get("projects", [])
            _pers_sens  = st.session_state.get("periods", [])
            if not _projs_sens or not _pers_sens:
                st.warning("Primero ejecuta la optimización principal para tener proyectos y períodos cargados.")
            else:
                with st.spinner("Corriendo 3 puntos del error de estimación..."):
                    try:
                        _cfg_sens = get_config()
                        _opt_sens = EnterpriseOptimizer(config=_cfg_sens)
                        _sens_res: SensitivityResult = _opt_sens.estimate_robustness(
                            projects=_projs_sens,
                            periods=_pers_sens,
                            top_n=int(_sk_topn),
                        )
                        st.session_state["sensitivity_result"] = _sens_res
                        st.rerun()
                    except Exception as _ek:
                        st.error(f"Error en el análisis de robustez: {_ek}")
                        with st.expander("Traceback"):
                            st.code(traceback.format_exc())

        _sens_res: Optional[SensitivityResult] = st.session_state.get("sensitivity_result")
        if _sens_res:
            _sk_pess, _sk_base, _sk_opt = _sens_res.scenarios
            _sk_k1, _sk_k2, _sk_k3, _sk_k4 = st.columns(4, gap="medium")
            with _sk_k1:
                metric_card(
                    "VNT · costos altos",
                    fmt_cop(_sk_pess.vnt_total_COP),
                    f"{_sk_pess.n_projects} proyectos · cada uno en su extremo alto",
                    EPM_RED,
                )
            with _sk_k2:
                metric_card(
                    "VNT Base",
                    fmt_cop(_sk_base.vnt_total_COP),
                    f"{_sk_base.n_projects} proyectos seleccionados",
                    EPM_GREEN,
                )
            with _sk_k3:
                metric_card(
                    "VNT · costos bajos",
                    fmt_cop(_sk_opt.vnt_total_COP),
                    f"{_sk_opt.n_projects} proyectos · cada uno en su extremo bajo",
                    EPM_TEAL,
                )
            with _sk_k4:
                _sk_range_col = EPM_RED if _sens_res.vnt_range_pct > 40 else (
                    EPM_AMBER if _sens_res.vnt_range_pct > 20 else EPM_GREEN)
                metric_card(
                    "Rango VNT del portafolio",
                    f"{_sens_res.vnt_range_pct:.1f}%",
                    "Ancho que produce la calidad de los estimativos",
                    _sk_range_col,
                )

            # ── De dónde viene el ancho: composición de bases declaradas ──────
            if _sens_res.class_mix:
                _mix_total = sum(_sens_res.class_mix.values()) or 1
                _mix_txt = " · ".join(
                    f"{_bands_ui.get(k, {}).get('label', k).split('—')[0].strip()}: "
                    f"{v} ({v/_mix_total*100:.0f}%)"
                    for k, v in sorted(_sens_res.class_mix.items())
                )
                _n_default = _sens_res.class_mix.get(_default_cls, 0)
                st.caption(
                    f"**Bases de estimativo declaradas:** {_mix_txt}. "
                    + (f"⚠️ {_n_default} proyecto(s) sin declarar base — se asumen en la "
                       f"clase más débil, que es lo que ensancha el rango. "
                       f"Declarar la base real de esos estimativos es lo que estrecha el análisis."
                       if _n_default else "")
                )

            # ── Gráfico tornado VNT ────────────────────────────────────────
            _sk_fig = go.Figure()
            _sk_labels = [s.label for s in _sens_res.scenarios]
            _sk_vnts   = [s.vnt_total_COP / 1e9 for s in _sens_res.scenarios]
            _sk_capexs = [s.capex_total_COP / 1e9 for s in _sens_res.scenarios]
            _sk_colors = [EPM_RED, EPM_GREEN, EPM_TEAL]

            _sk_fig.add_trace(go.Bar(
                name="VNT (B COP)",
                x=_sk_labels, y=_sk_vnts,
                marker_color=_sk_colors,
                text=[f"${v:.3f}B" for v in _sk_vnts],
                textposition="outside",
                hovertemplate="%{x}<br>VNT: $%{y:.3f}B COP<extra></extra>",
            ))
            _sk_fig.add_trace(go.Scatter(
                name="CAPEX (B COP)",
                x=_sk_labels, y=_sk_capexs,
                mode="lines+markers+text",
                line=dict(color=EPM_AMBER, width=2, dash="dot"),
                marker=dict(size=10, symbol="diamond"),
                text=[f"${v:.3f}B" for v in _sk_capexs],
                textposition="top center",
                textfont=dict(size=9, color=EPM_AMBER),
                yaxis="y2",
                hovertemplate="%{x}<br>CAPEX: $%{y:.3f}B COP<extra></extra>",
            ))
            _sk_fig.update_layout(
                height=320,
                margin=dict(t=40, b=40, l=70, r=80),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(15,23,42,0.6)",
                font=dict(family="Inter,Arial", size=11, color=EPM_SLATE),
                yaxis=dict(
                    title="VNT (Billones COP)",
                    gridcolor="rgba(255,255,255,0.05)",
                    tickformat="$.3f",
                ),
                yaxis2=dict(
                    title="CAPEX (Billones COP)",
                    overlaying="y", side="right",
                    gridcolor="rgba(0,0,0,0)",
                    tickformat="$.3f",
                    showgrid=False,
                ),
                title=dict(
                    text="VNT y CAPEX en los extremos del error de estimación",
                    font=dict(size=11, color="#CBD5E1"), x=0,
                ),
                legend=dict(orientation="h", y=-0.18, font=dict(size=10, color="#CBD5E1")),
                barmode="group",
            )
            st.plotly_chart(_sk_fig, use_container_width=True)

            # ── Tabla de robustez ──────────────────────────────────────────
            st.markdown("##### Robustez por proyecto")
            st.caption(
                "Se clasifican **todos** los proyectos evaluados, no solo los del top-N. "
                "🟢 **ESTABLE** = seleccionado en los 3 puntos y sin movimiento relevante "
                "de posición · 🟡 **SENSIBLE** = se mueve · 🔴 **INESTABLE** = su presencia "
                "depende del error de estimación · ⚪ **NUNCA SELECCIONADO** = fuera del "
                "portafolio en los 3 puntos."
            )

            _proj_by_id  = {p.id: p for p in st.session_state.get("projects", [])}
            _proj_names  = {pid: p.name for pid, p in _proj_by_id.items()}

            _stab_icon_map = {
                "ESTABLE":            "🟢",
                "SENSIBLE":           "🟡",
                "INESTABLE":          "🔴",
                "NUNCA_SELECCIONADO": "⚪",
            }
            _rank_pts = _sens_res.rank_by_point or {}

            _stab_rows = []
            for _pid, _stab in _sens_res.stability.items():
                _p   = _proj_by_id.get(_pid)
                _cls = (getattr(_p.costs, "estimate_class", None) or _default_cls) if _p else _default_cls
                _bd  = _bands_ui.get(_cls, {})
                _rk  = [_rank_pts[lbl].get(_pid) for lbl in _rank_pts if _pid in _rank_pts[lbl]]
                _mov = (max(_rk) - min(_rk)) if len(_rk) == 3 else None
                _stab_rows.append({
                    "Estado":   f"{_stab_icon_map.get(_stab,'·')} {_stab.replace('_',' ').title()}",
                    "Proyecto": _proj_names.get(_pid, _pid)[:38],
                    "Base estimativo": _bd.get("label", _cls).split("—")[0].strip(),
                    "Banda CAPEX": (f"{_bd.get('low',0)*100:+.0f}% / {_bd.get('high',0)*100:+.0f}%"
                                    if _bd else "—"),
                    "Costos altos": (f"#{_rank_pts.get('Costos altos',{}).get(_pid)}"
                                     if _pid in _rank_pts.get("Costos altos", {}) else "—"),
                    "Base":         (f"#{_rank_pts.get('Base',{}).get(_pid)}"
                                     if _pid in _rank_pts.get("Base", {}) else "—"),
                    "Costos bajos": (f"#{_rank_pts.get('Costos bajos',{}).get(_pid)}"
                                     if _pid in _rank_pts.get("Costos bajos", {}) else "—"),
                    "Δ posición":   (f"{_mov}" if _mov is not None else "—"),
                })

            _order = {"Inestable": 0, "Sensible": 1, "Estable": 2, "Nunca Seleccionado": 3}
            _stab_rows.sort(key=lambda r: _order.get(
                r["Estado"].split(" ", 1)[-1], 4))

            _df_stab = pd.DataFrame(_stab_rows)
            if not _df_stab.empty:
                st.dataframe(_df_stab.head(int(_sk_topn) * 3),
                             use_container_width=True, hide_index=True)

            # ── Interpretación ─────────────────────────────────────────────
            _n_estable   = sum(1 for v in _sens_res.stability.values() if v == "ESTABLE")
            _n_sensible  = sum(1 for v in _sens_res.stability.values() if v == "SENSIBLE")
            _n_inestable = sum(1 for v in _sens_res.stability.values() if v == "INESTABLE")
            _n_nunca     = sum(1 for v in _sens_res.stability.values() if v == "NUNCA_SELECCIONADO")
            _total_sel   = _n_estable + _n_sensible + _n_inestable

            if _total_sel > 0:
                _pct_estable = _n_estable / _total_sel * 100
                if _pct_estable >= 70:
                    _stab_msg = ("El portafolio es <b>robusto</b>: la selección se sostiene "
                                 "aunque los estimativos se muevan dentro de sus bandas.")
                    _stab_style = f"background:#ECFDF5;border-left:4px solid {EPM_GREEN}"
                elif _pct_estable >= 40:
                    _stab_msg = ("El portafolio es <b>moderadamente sensible</b>. Los proyectos "
                                 "marcados son los que conviene reestimar antes de comprometer cupo.")
                    _stab_style = f"background:#FFFBEB;border-left:4px solid {EPM_AMBER}"
                else:
                    _stab_msg = ("El portafolio es <b>altamente sensible</b> al error de estimación. "
                                 "La palanca no es correr más análisis: es mejorar la base de los "
                                 "estimativos de los proyectos marcados en rojo.")
                    _stab_style = f"background:#FEF2F2;border-left:4px solid {EPM_RED}"

                st.markdown(
                    f'<div style="{_stab_style};border-radius:0 8px 8px 0;'
                    f'padding:10px 16px;font-size:0.78rem;color:{EPM_SLATE};margin-top:8px">'
                    f'{_stab_msg} '
                    f'{_n_estable} estables · {_n_sensible} sensibles · {_n_inestable} inestables '
                    f'de {_total_sel} seleccionados ({_n_nunca} nunca entran). '
                    f'Rango VNT del portafolio: <b>{_sens_res.vnt_range_pct:.1f}%</b>.'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # ══════════════════════════════════════════════════════════════════════
        # FRONTERA EFICIENTE — tratamiento de la incertidumbre presupuestal
        # A nivel de portafolio la incertidumbre dominante no es cuánto cuesta
        # cada proyecto (errores independientes se compensan entre muchos) sino
        # cuánto cupo va a haber. Esa no se diversifica y nadie la controla.
        # La frontera la trata entregando el plan óptimo para CADA presupuesto
        # posible y el valor marginal de moverse entre ellos.
        # ══════════════════════════════════════════════════════════════════════
        st.divider()
        section_bar("📈 Frontera Eficiente — VNT vs. Cupo de Inversión")
        st.caption(
            "La frontera **es** el tratamiento de la incertidumbre presupuestal: en lugar "
            "de suponer un cupo cierto y entregar un plan, entrega el plan óptimo para cada "
            "cupo posible y cuánto valor compra cada peso adicional. El **punto de quiebre** "
            "marca dónde los rendimientos empiezan a decrecer — es la cifra de presupuesto "
            "defendible técnicamente."
        )

        # ── Frontera calculada por el núcleo con la corrida principal ─────────
        _core_fr = [p for p in (getattr(result, "efficient_frontier", None) or [])
                    if "error" not in p]
        if _core_fr:
            _df_fr = pd.DataFrame(_core_fr)
            _knee  = next((p for p in _core_fr if p.get("is_knee")), None)
            _base  = next((p for p in _core_fr if p.get("is_baseline")), None)

            _fk1, _fk2, _fk3 = st.columns(3, gap="medium")
            with _fk1:
                metric_card(
                    "Cupo declarado",
                    fmt_cop(_base["budget_COP"]) if _base else "—",
                    (f"VNT {fmt_cop(_base['vnt_COP'])} · {_base['n_projects']} proyectos"
                     if _base else "—"),
                    EPM_DARK,
                )
            with _fk2:
                _mv_base = (_base or {}).get("marginal_value")
                metric_card(
                    "Valor marginal del cupo",
                    f"{_mv_base:.2f}×" if _mv_base is not None else "—",
                    "COP de VNT por cada COP adicional de cupo",
                    EPM_AMBER,
                )
            with _fk3:
                metric_card(
                    "Punto de quiebre",
                    f"{_knee['budget_pct']*100:.0f}% del cupo" if _knee else "no detectado",
                    (f"{fmt_cop(_knee['budget_COP'])} · más allá el valor cae"
                     if _knee else "curva sin quiebre claro"),
                    EPM_GREEN if _knee else EPM_SLATE,
                )

            _fig_fr = go.Figure()
            _fig_fr.add_trace(go.Scatter(
                x=(_df_fr["budget_COP"] / 1e9), y=(_df_fr["vnt_COP"] / 1e9),
                mode="lines+markers", name="Frontera eficiente",
                line=dict(color=EPM_GREEN, width=2.5, shape="hv"),
                marker=dict(size=7, color=EPM_DARK),
                hovertemplate=("Cupo: $%{x:,.1f}B<br>VNT: $%{y:,.1f}B"
                               "<extra></extra>"),
            ))
            if _base:
                _fig_fr.add_trace(go.Scatter(
                    x=[_base["budget_COP"] / 1e9], y=[_base["vnt_COP"] / 1e9],
                    mode="markers", name="Cupo declarado",
                    marker=dict(size=16, color=EPM_AMBER, symbol="star",
                                line=dict(width=1, color="#1E293B")),
                    hovertemplate="★ Cupo declarado<extra></extra>",
                ))
            if _knee:
                _fig_fr.add_trace(go.Scatter(
                    x=[_knee["budget_COP"] / 1e9], y=[_knee["vnt_COP"] / 1e9],
                    mode="markers", name="Punto de quiebre",
                    marker=dict(size=15, color=EPM_RED, symbol="diamond",
                                line=dict(width=1, color="#1E293B")),
                    hovertemplate=("◆ Quiebre — "
                                   f"{_knee['budget_pct']*100:.0f}% del cupo<extra></extra>"),
                ))
            _fig_fr.update_layout(
                height=360,
                xaxis_title="Cupo de inversión (Billones COP)",
                yaxis_title="VNT total del portafolio (Billones COP)",
                legend=dict(orientation="h", y=-0.22),
                margin=dict(t=10, b=60, l=60, r=20),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(15,23,42,0.6)",
                font=dict(family="Inter,Arial", size=11, color=EPM_SLATE),
            )
            st.plotly_chart(_fig_fr, use_container_width=True)

            # Valor marginal por tramo — dónde deja de rendir el peso adicional
            _fig_mv = go.Figure()
            _mv_rows = [p for p in _core_fr if p.get("marginal_value") is not None]
            if _mv_rows:
                _fig_mv.add_trace(go.Bar(
                    x=[p["budget_pct"] * 100 for p in _mv_rows],
                    y=[p["marginal_value"] for p in _mv_rows],
                    marker_color=[EPM_RED if p.get("is_knee") else EPM_TEAL
                                  for p in _mv_rows],
                    name="Valor marginal",
                    hovertemplate=("Cupo: %{x:.0f}%<br>"
                                   "%{y:.2f} COP de VNT por COP de cupo<extra></extra>"),
                ))
                _fig_mv.add_hline(y=1.0, line_dash="dot", line_color=EPM_AMBER,
                                  annotation_text="1× — el peso se paga a sí mismo",
                                  annotation_font=dict(size=9, color=EPM_AMBER))
                _fig_mv.update_layout(
                    height=240,
                    xaxis_title="Cupo (% del declarado)",
                    yaxis_title="VNT ganado por peso de cupo",
                    margin=dict(t=10, b=50, l=60, r=20),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(15,23,42,0.6)",
                    font=dict(family="Inter,Arial", size=11, color=EPM_SLATE),
                    showlegend=False,
                )
                st.plotly_chart(_fig_mv, use_container_width=True)

            with st.expander("📋 Tabla de la frontera"):
                _df_fr_show = pd.DataFrame([{
                    "Cupo (%)":      f"{p['budget_pct']*100:.0f}%",
                    "Cupo (MM COP)": p["budget_COP"] / 1e6,
                    "VNT (MM COP)":  p["vnt_COP"] / 1e6,
                    "Proyectos":     p["n_projects"],
                    "Valor marginal": (round(p["marginal_value"], 3)
                                       if p.get("marginal_value") is not None else None),
                    "Hito": ("★ cupo declarado" if p.get("is_baseline")
                             else "◆ quiebre" if p.get("is_knee") else ""),
                } for p in _core_fr])
                st.dataframe(_df_fr_show, use_container_width=True, hide_index=True)

            st.caption(
                "**Valor marginal** = ΔVNT / Δcupo entre puntos consecutivos. Es el precio "
                "sombra correcto del presupuesto: en un MILP la dualidad lineal no se "
                "sostiene, así que el dual del solver no es un valor marginal válido y se "
                "calcula por diferencia finita. La curva es escalonada porque el problema "
                "es entero — un proyecto entra completo o no entra."
            )
        else:
            st.info(
                "La frontera se calcula junto con la optimización principal. "
                "Ejecuta el optimizador para verla.", icon="📈")

        # ── Barrido manual — refinamiento sobre un rango específico ───────────
        st.markdown("##### Barrido manual")
        st.caption(
            "Refina la frontera sobre un rango y paso específicos. Útil para acotar el "
            "punto de quiebre con más resolución que el barrido automático."
        )

        _sw_c1, _sw_c2, _sw_c3, _sw_c4 = st.columns(4)
        with _sw_c1:
            _sw_min = st.slider("Cupo mínimo (%)", 40, 90, 60, step=10,
                                key="sw_min")
        with _sw_c2:
            _sw_max = st.slider("Cupo máximo (%)", 100, 180, 130, step=10,
                                key="sw_max")
        with _sw_c3:
            _sw_step = st.selectbox("Paso (%)", [5, 10, 15, 20], index=1,
                                    key="sw_step")
        with _sw_c4:
            _sw_tl = st.number_input("Límite / corrida (s)", 10, 120, 30,
                                     key="sw_tl")

        _sw_pcts = list(range(int(_sw_min), int(_sw_max) + 1, int(_sw_step)))
        st.caption(
            f"Se correrán **{len(_sw_pcts)} escenarios** "
            f"({', '.join(str(p)+'%' for p in _sw_pcts)})"
        )

        _run_sweep = st.button("⚡ Ejecutar Barrido Presupuestal",
                               type="secondary", key="run_budget_sweep")

        if _run_sweep and PULP_OK:
            # Determinar proyectos en alcance (misma lógica que EJECUTAR)
            if scenario == "local" and group_id_local:
                _projs_sw = [p for p in projects
                             if getattr(p, "exclusion_group_id", None) == group_id_local]
            elif scenario == "silo" and bu_filter_opt:
                _projs_sw = [p for p in projects
                             if p.business_unit == bu_filter_opt]
            else:
                _projs_sw = projects

            _sweep_results = []
            _sweep_prog = st.progress(0, text="Iniciando barrido...")
            try:
                for _si, _pct in enumerate(_sw_pcts):
                    _sweep_prog.progress(
                        _si / len(_sw_pcts),
                        text=f"Corriendo {_pct}% del cupo... ({_si+1}/{len(_sw_pcts)})"
                    )
                    _periods_sw = [
                        ResourcePeriod(
                            year=yr,
                            budget_COP=capex_total_by_yr.get(yr, 0) * (_pct / 100),
                            max_person_months=float(max_pm),
                            max_projects_concurrent=int(max_conc),
                        )
                        for yr in years_cfg
                    ]
                    try:
                        _cfg_sw = copy.deepcopy(cfg)
                        _opt_sw = EnterpriseOptimizer(config=_cfg_sw)
                        _r_sw = _opt_sw.optimize(
                            _projs_sw, _periods_sw,
                            solver_time_limit_s=int(_sw_tl),
                            compute_shadow_prices=False,
                            compute_frontier=False,
                        )
                        _sweep_results.append({
                            "pct":       _pct,
                            "vnt_COP":   _r_sw.total_net_value,
                            "capex_COP": _r_sw.total_capex,
                            "n_proj":    len(_r_sw.selected),
                            "status":    _r_sw.solver_status,
                        })
                    except Exception:
                        _sweep_results.append({
                            "pct": _pct, "vnt_COP": None,
                            "capex_COP": None, "n_proj": 0, "status": "ERROR",
                        })
                _sweep_prog.progress(1.0, text="✅ Barrido completado")
                st.session_state["sensitivity_sweep"] = _sweep_results
                st.rerun()
            except Exception as _sw_err:
                st.error(f"Error en el barrido: {_sw_err}")

        _sweep_data = st.session_state.get("sensitivity_sweep")
        if _sweep_data:
            _valid_sw = [d for d in _sweep_data if d["vnt_COP"] is not None]
            if _valid_sw:
                _df_sw = pd.DataFrame(_valid_sw).sort_values("pct").reset_index(drop=True)
                _df_sw["VNT (MM COP)"]   = _df_sw["vnt_COP"] / 1e6
                _df_sw["CAPEX (MM COP)"] = _df_sw["capex_COP"] / 1e6

                # Shadow price empírico: ΔVNT / ΔCAPEX entre puntos consecutivos
                _dv = _df_sw["vnt_COP"].diff()
                _dc = _df_sw["capex_COP"].diff()
                _df_sw["SP empírico (COP/COP)"] = (
                    _dv / _dc.replace(0, float("nan"))
                ).round(4)

                # Gráfico 1: curva VNT vs. cupo
                _fig_sw = go.Figure()
                _fig_sw.add_trace(go.Scatter(
                    x=_df_sw["pct"],
                    y=_df_sw["VNT (MM COP)"],
                    mode="lines+markers",
                    line=dict(color=EPM_GREEN, width=2.5),
                    marker=dict(size=9, color=EPM_DARK, symbol="circle"),
                    name="VNT total (MM COP)",
                    hovertemplate=(
                        "Cupo: %{x}%<br>VNT: %{y:,.0f} MM COP<extra></extra>"
                    ),
                ))
                _cur_row = _df_sw[_df_sw["pct"] == budget_pct]
                if not _cur_row.empty:
                    _fig_sw.add_trace(go.Scatter(
                        x=_cur_row["pct"].tolist(),
                        y=_cur_row["VNT (MM COP)"].tolist(),
                        mode="markers",
                        marker=dict(size=14, color=EPM_AMBER, symbol="star"),
                        name=f"Cupo actual ({budget_pct}%)",
                        hovertemplate=(
                            f"★ Actual ({budget_pct}%): "
                            "%{y:,.0f} MM COP<extra></extra>"
                        ),
                    ))
                _fig_sw.update_layout(
                    height=340,
                    xaxis_title="Cupo presupuestal (%)",
                    yaxis_title="VNT total (MM COP)",
                    legend=dict(orientation="h", y=-0.25),
                    margin=dict(t=10, b=60),
                    paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(_fig_sw, use_container_width=True)

                # Gráfico 2: N° proyectos + shadow price empírico
                _fig_sw2 = go.Figure()
                _fig_sw2.add_trace(go.Bar(
                    x=_df_sw["pct"],
                    y=_df_sw["n_proj"],
                    name="N° proyectos",
                    marker_color=EPM_DARK,
                    opacity=0.75,
                    yaxis="y",
                    hovertemplate="Cupo: %{x}%<br>Proyectos: %{y}<extra></extra>",
                ))
                _fig_sw2.add_trace(go.Scatter(
                    x=_df_sw["pct"],
                    y=_df_sw["SP empírico (COP/COP)"],
                    name="Shadow price empírico",
                    mode="lines+markers",
                    line=dict(color=EPM_AMBER, width=2, dash="dot"),
                    marker=dict(size=7),
                    yaxis="y2",
                    hovertemplate="Cupo: %{x}%<br>SP: %{y:.4f} COP/COP<extra></extra>",
                ))
                _fig_sw2.update_layout(
                    height=280,
                    xaxis_title="Cupo presupuestal (%)",
                    yaxis=dict(title="N° proyectos", side="left"),
                    yaxis2=dict(
                        title="SP empírico (COP/COP)",
                        overlaying="y", side="right", showgrid=False,
                    ),
                    legend=dict(orientation="h", y=-0.35),
                    margin=dict(t=10, b=70),
                    paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(_fig_sw2, use_container_width=True)

                with st.expander("📋 Tabla detallada del barrido"):
                    _df_sw_show = _df_sw[[
                        "pct", "VNT (MM COP)", "CAPEX (MM COP)",
                        "n_proj", "SP empírico (COP/COP)", "status",
                    ]].rename(columns={"pct": "Cupo (%)", "n_proj": "Proyectos"})
                    st.dataframe(
                        _df_sw_show.style.format({
                            "VNT (MM COP)":          "{:,.0f}",
                            "CAPEX (MM COP)":        "{:,.0f}",
                            "SP empírico (COP/COP)": "{:.4f}",
                        }),
                        use_container_width=True,
                    )

                st.caption(
                    "**Shadow price empírico** = ΔVNT / ΔCAPEX entre puntos "
                    "consecutivos. Indica cuántos COP de VNT se agregan por cada "
                    "COP adicional de cupo. Cuando se acerca a 0 el portafolio "
                    "está saturado y ampliar el cupo ya no genera valor."
                )

        # ══════════════════════════════════════════════════════════════════════
        # MÓDULO 5 — ESCENARIOS SISTÉMICOS: Comparación de Portafolios
        # Fiel a Copperleaf C55 / Anglian Water: guarda múltiples resultados
        # de optimización con nombre y los compara (VNT, CAPEX, proyectos).
        # Equivalente a "Base", "COVID replanificar", "−10% CAPEX" (Video 3).
        # ══════════════════════════════════════════════════════════════════════
        st.divider()
        section_bar("🗂 Escenarios Sistémicos — Comparación de Portafolios")
        st.caption(
            "Guarda el resultado de optimización actual con un nombre y compáralo "
            "contra otros escenarios (ej. Base, Restricción −20%, Crisis Presupuestal). "
            "Fiel al módulo de escenarios de Copperleaf C55 / Anglian Water."
        )

        if "saved_scenarios" not in st.session_state:
            st.session_state["saved_scenarios"] = {}

        # ── Guardar escenario actual ──────────────────────────────────────────
        _sc_col1, _sc_col2 = st.columns([3, 1])
        with _sc_col1:
            _sc_name = st.text_input(
                "Nombre del escenario",
                value=f"Escenario {budget_pct}% — {scenario_run.upper()}",
                key="sc_name_input",
                label_visibility="collapsed",
                placeholder="Nombre del escenario a guardar…",
            )
        with _sc_col2:
            _save_sc = st.button("💾 Guardar Escenario",
                                 use_container_width=True,
                                 key="save_scenario_btn")

        if _save_sc and _sc_name.strip():
            _sc_key = _sc_name.strip()
            _capex_by_yr_sc = {
                yr: bu_data.get("used_COP", 0)
                for yr, bu_data in result.budget_utilization.items()
            }
            st.session_state["saved_scenarios"][_sc_key] = {
                "name":         _sc_key,
                "budget_pct":   budget_pct,
                "scenario":     scenario_run,
                "vnt_COP":      result.total_net_value,
                "capex_COP":    result.total_capex,
                "n_proj":       len(result.selected),
                "n_postponed":  len(result.postponed),
                "status":       result.solver_status,
                "solve_time":   result.solve_time_s,
                "capex_by_yr":  _capex_by_yr_sc,
                "selected_ids": [r["project_id"] for r in result.selected],
                # ── Detalle por proyecto (Scenario Duel §4 — VNT waterfall) ──
                "projects_detail": [
                    {
                        "id":           r.get("project_id", ""),
                        "name":         r.get("project_name", r.get("project_id", "")),
                        "bu":           r.get("business_unit", ""),
                        "vnt_COP":      r.get("net_value_COP", 0.0),
                        "capex_COP":    r.get("capex_COP", 0.0),
                        "year":         r.get("year", 0),
                    }
                    for r in result.selected
                ],
                # ── Shadow prices y utilización (Capacity Gap §3 por escenario) ──
                "shadow_prices":    dict(result.shadow_prices or {}),
                "budget_utilization": {
                    str(yr): {"available": d.get("available_COP", 0),
                               "used":      d.get("used_COP", 0)}
                    for yr, d in (result.budget_utilization or {}).items()
                },
            }
            st.success(f"✅ Escenario «{_sc_key}» guardado.")

        _saved = st.session_state["saved_scenarios"]

        # ── Eliminar escenario guardado ───────────────────────────────────────
        if _saved:
            _del_opts = ["(seleccionar para eliminar)"] + list(_saved.keys())
            _del_sel  = st.selectbox("", _del_opts, key="sc_del_sel",
                                     label_visibility="collapsed")
            if _del_sel != "(seleccionar para eliminar)":
                if st.button(f"🗑 Eliminar «{_del_sel}»", key="sc_del_btn"):
                    del st.session_state["saved_scenarios"][_del_sel]
                    st.rerun()

        # ── Panel de comparación (≥ 2 escenarios) ────────────────────────────
        if len(_saved) >= 2:
            st.markdown("---")
            st.markdown("#### 📊 Comparación de Escenarios")

            _sc_names  = list(_saved.keys())
            _sc_colors = [EPM_GREEN, EPM_AMBER, EPM_DARK,
                          "#6366F1", "#EC4899", "#14B8A6"]

            # KPI cards (hasta 4)
            _esc_cols = st.columns(min(len(_sc_names), 4))
            for _ei, _ek in enumerate(_sc_names[:4]):
                _ed = _saved[_ek]
                with _esc_cols[_ei]:
                    metric_card(
                        _ek[:26],
                        f"{_ed['vnt_COP']/1e9:.2f}B COP",
                        f"{_ed['n_proj']} proy. · {_ed['budget_pct']}% cupo",
                        _sc_colors[_ei % len(_sc_colors)],
                    )

            # Gráfico 1: VNT total por escenario
            _fig_sc = go.Figure()
            _fig_sc.add_trace(go.Bar(
                x=_sc_names,
                y=[_saved[k]["vnt_COP"] / 1e6 for k in _sc_names],
                name="VNT total (MM COP)",
                marker_color=[_sc_colors[i % len(_sc_colors)]
                               for i in range(len(_sc_names))],
                text=[f"{_saved[k]['vnt_COP']/1e6:,.0f}"
                       for k in _sc_names],
                textposition="outside",
                hovertemplate="%{x}<br>VNT: %{y:,.0f} MM COP<extra></extra>",
            ))
            _fig_sc.update_layout(
                height=320,
                yaxis_title="VNT total (MM COP)",
                showlegend=False,
                margin=dict(t=30, b=40),
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(_fig_sc, use_container_width=True)

            # Gráfico 2: CAPEX comprometido por vigencia y escenario
            _yr_union = sorted(set(
                yr for d in _saved.values()
                for yr in d.get("capex_by_yr", {}).keys()
            ))
            if _yr_union:
                _fig_yr = go.Figure()
                for _ei, _ek in enumerate(_sc_names):
                    _fig_yr.add_trace(go.Bar(
                        x=[str(y) for y in _yr_union],
                        y=[_saved[_ek].get("capex_by_yr", {}).get(yr, 0) / 1e6
                            for yr in _yr_union],
                        name=_ek[:22],
                        marker_color=_sc_colors[_ei % len(_sc_colors)],
                        hovertemplate=(
                            f"{_ek}<br>Año: %{{x}}<br>"
                            "CAPEX: %{y:,.0f} MM COP<extra></extra>"
                        ),
                    ))
                _fig_yr.update_layout(
                    barmode="group",
                    height=300,
                    xaxis_title="Vigencia",
                    yaxis_title="CAPEX comprometido (MM COP)",
                    legend=dict(orientation="h", y=-0.3),
                    margin=dict(t=10, b=70),
                    paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(_fig_yr, use_container_width=True)

            # Tabla resumen
            _df_sc = pd.DataFrame([{
                "Escenario":      k,
                "Cupo (%)":       _saved[k]["budget_pct"],
                "VNT (MM COP)":   round(_saved[k]["vnt_COP"] / 1e6, 1),
                "CAPEX (MM COP)": round(_saved[k]["capex_COP"] / 1e6, 1),
                "Proyectos":      _saved[k]["n_proj"],
                "Postergados":    _saved[k]["n_postponed"],
                "VNT/CAPEX":      round(
                    _saved[k]["vnt_COP"] / max(_saved[k]["capex_COP"], 1), 3
                ),
                "Solver":         _saved[k]["status"],
            } for k in _sc_names])

            st.dataframe(
                _df_sc.style.format({
                    "VNT (MM COP)":   "{:,.1f}",
                    "CAPEX (MM COP)": "{:,.1f}",
                    "VNT/CAPEX":      "{:.3f}",
                }).highlight_max(
                    subset=["VNT (MM COP)", "Proyectos", "VNT/CAPEX"],
                    color="#D1FAE5",
                ).highlight_min(
                    subset=["CAPEX (MM COP)", "Postergados"],
                    color="#D1FAE5",
                ),
                use_container_width=True,
            )

            # Proyectos exclusivos por escenario
            with st.expander("🔍 Proyectos exclusivos por escenario"):
                for _ek in _sc_names:
                    _ids_k  = set(_saved[_ek]["selected_ids"])
                    _others = set(
                        pid
                        for _ok, _od in _saved.items()
                        if _ok != _ek
                        for pid in _od["selected_ids"]
                    )
                    _only_k = _ids_k - _others
                    if _only_k:
                        st.markdown(
                            f"**{_ek}** selecciona exclusivamente: "
                            + ", ".join(sorted(_only_k))
                        )
                    else:
                        st.markdown(
                            f"**{_ek}** — todos sus proyectos "
                            "aparecen en al menos otro escenario."
                        )

        elif len(_saved) == 1:
            st.info(
                "Un escenario guardado. Cambia los parámetros, ejecuta la "
                "optimización y guarda un segundo escenario para activar "
                "la comparación."
            )
        else:
            st.info(
                "Ejecuta la optimización, ajusta los parámetros (ej. cupo, "
                "escenario) y guarda cada resultado como un escenario. "
                "La comparación aparecerá aquí automáticamente."
            )

# ══════════════════════════════════════════════════════════════════════════════
# TAB AUDITORÍA — §1 Snapshots ISO 55001  (v12.2: pestaña principal Auditoría)
# ══════════════════════════════════════════════════════════════════════════════
with _tab_audi:
    page_header("Trazabilidad y Auditoría",
                "Snapshots inmutables · SHA-256 · Comparación de evaluaciones",
                "ISO 55001:2024 §4.5.5")

    snap_dir = st.text_input("Directorio de snapshots", "./snapshots")
    store = SnapshotStore(snap_dir)
    st.session_state["snapshot_store"] = store

    snaps = store.search()

    if not snaps:
        st.info("No hay snapshots registrados aún. Evalúa un proyecto en la pestaña "
                "**Proyecto Individual** y guarda el snapshot.")
    else:
        # KPIs
        s1, s2, s3 = st.columns(3)
        with s1: metric_card("Total Snapshots", str(len(snaps)), "Registros inmutables")
        with s2:
            analistas = set(s.get("analyst","") for s in snaps)
            metric_card("Analistas", str(len(analistas)),
                         " · ".join(list(analistas)[:3]))
        with s3:
            versions = set(s.get("config_version","") for s in snaps)
            metric_card("Versiones Config", str(len(versions)),
                         " · ".join(sorted(versions)))

        st.divider()

        # ── Búsqueda ─────────────────────────────────────────────────────────
        sb1, sb2 = st.columns(2)
        with sb1:
            search_pid = st.text_input("Buscar por ID de proyecto", "")
        with sb2:
            search_analyst = st.text_input("Buscar por analista", "")

        filtered_snaps = store.search(
            project_id=search_pid or None,
            analyst=search_analyst or None,
        )

        # ── Tabla de snapshots ────────────────────────────────────────────────
        # Detectar duplicados por content_hash (mismos inputs+outputs, distinto UUID)
        _ch_counter: dict = {}
        for _s in filtered_snaps:
            _ch = _s.get("content_hash", "")
            if _ch:
                _ch_counter[_ch] = _ch_counter.get(_ch, 0) + 1

        df_snaps = pd.DataFrame([{
            "Snapshot ID":  s["snapshot_id"][:12] + "…",
            "Timestamp":    s["timestamp_utc"][:19],
            "Proyecto":     s["project_id"],
            "Problema":     (s.get("problem_description","") or "")[:48],
            "Analista":     s.get("analyst",""),
            "VNT (MM)":     round(s.get("net_value_COP",0)/1e6, 1),
            "VEI":          round(s.get("vei",0), 3),
            "Config":       s.get("config_version","?"),
            "Razón":        s.get("change_reason","")[:40],
            # content_hash: "Sí" si el mismo resultado ya existe en otro snapshot
            "Duplicado":    "⚠ Sí" if _ch_counter.get(s.get("content_hash",""), 0) > 1
                            else ("—" if not s.get("content_hash") else "✅ No"),
        } for s in filtered_snaps])

        # Resaltar filas duplicadas en naranja
        def _hl_dup(val):
            return "background-color:#78350f22;color:#F59E0B;font-weight:600" \
                   if str(val).startswith("⚠") else ""

        st.dataframe(
            df_snaps.style.applymap(_hl_dup, subset=["Duplicado"])
            if hasattr(df_snaps.style, "applymap") else
            df_snaps.style.map(_hl_dup, subset=["Duplicado"]),
            use_container_width=True, height=360
        )
        # KPI de duplicados detectados
        _n_dup_content = sum(1 for s in filtered_snaps
                             if _ch_counter.get(s.get("content_hash",""), 0) > 1)
        if _n_dup_content:
            st.markdown(
                f'<div class="box-warn">⚠ <b>{_n_dup_content} snapshot(s) duplicados</b> '
                f'— mismo resultado evaluado más de una vez. El campo <code>content_hash</code> '
                f'permite identificarlos para deduplicación.</div>',
                unsafe_allow_html=True
            )
        else:
            st.caption("✅ Sin duplicados detectados por content_hash.")

        # ── Verificar integridad ──────────────────────────────────────────────
        section_bar("🔐 Verificación de Integridad SHA-256")
        if st.button("🔍 Verificar todos los snapshots"):
            with st.spinner("Verificando hashes..."):
                all_ok = True
                for s in snaps:
                    valid_hash, msg = store.verify(s["snapshot_id"])
                    if not valid_hash:
                        all_ok = False
                        st.markdown(
                            f'<div class="box-err">❌ {s["snapshot_id"][:12]}: {msg}</div>',
                            unsafe_allow_html=True
                        )
                if all_ok:
                    st.markdown(
                        f'<div class="box-ok">✅ Todos los {len(snaps)} snapshots '
                        f'tienen integridad verificada.</div>',
                        unsafe_allow_html=True
                    )

        # ── Comparar dos snapshots ────────────────────────────────────────────
        section_bar("🔄 Comparar Dos Snapshots")
        snap_ids = [s["snapshot_id"] for s in snaps]
        if len(snap_ids) >= 2:
            cc1, cc2 = st.columns(2)
            with cc1:
                sid1 = st.selectbox("Snapshot 1", snap_ids, key="sid1")
            with cc2:
                sid2 = st.selectbox("Snapshot 2", snap_ids,
                                     index=min(1, len(snap_ids)-1), key="sid2")
            if st.button("🔄 Comparar") and sid1 != sid2:
                diff = store.compare(sid1, sid2)
                summary = diff.get("summary", {})
                st.metric("ΔVNT",
                           fmt_cop(summary.get("vnt_delta_COP",0)),
                           f"{summary.get('vnt_delta_pct',0):.1f}%")
                if diff.get("input_changes"):
                    st.write("**Cambios en inputs:**")
                    for ch in diff["input_changes"][:10]:
                        st.code(
                            f"{ch['field']}: {ch['snapshot_1']} → {ch['snapshot_2']}",
                            language=None
                        )

        # ── Exportar reporte de auditoría ─────────────────────────────────────
        section_bar("📄 Exportar Reporte de Auditoría")
        if st.button("📥 Generar reporte JSON (ISO 55001 §4.5.5)"):
            report_path = store.export_audit_report(
                f"./audit_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            with open(report_path, "r") as f:
                report_json = f.read()
            st.download_button(
                label="⬇️ Descargar reporte de auditoría",
                data=report_json,
                file_name=Path(report_path).name,
                mime="application/json",
            )

# ══════════════════════════════════════════════════════════════════════════════
# TAB AUDITORÍA — §2 Degradación Predictiva Weibull  (Módulo 1)
# ══════════════════════════════════════════════════════════════════════════════
with _tab_audi:
    st.divider()
    page_header(
        "Degradación Predictiva de Activos",
        "Curva Weibull · PoF(t) · EEOL · Costo de Riesgo Anual (CRA)",
        "ISO 55001:2024 §6.6 · IEEE Std 493",
    )

    _cfg_wb   = get_config()
    _deg_cfg  = _cfg_wb.get("degradation_engine", {})
    # v3.7+: weibull_beta_by_class values son dicts {"beta":X,"_eta_años":Y,"_fuente":"..."}
    # Soporte backward-compatible con valores escalares legacy
    _wb_raw   = _deg_cfg.get("weibull_beta_by_class", {})
    _beta_map  = {}
    _eta_cfg_map  = {}   # η bibliográfico por clase (puede sobrescribir asset_horizons)
    _fuente_map   = {}   # fuente bibliográfica por clase (para tooltip Expert)
    for _k, _v in _wb_raw.items():
        if _k.startswith("_"):
            continue
        if isinstance(_v, dict):
            _beta_map[_k]     = float(_v.get("beta", 2.5))
            _eta_cfg_map[_k]  = float(_v.get("_eta_años", 0))
            _fuente_map[_k]   = str(_v.get("_fuente", ""))
        else:
            _beta_map[_k]     = float(_v)

    _cons_map = {}
    for _k, _v in _deg_cfg.get("consequence_by_class", {}).items():
        if _k.startswith("_"):
            continue
        if isinstance(_v, (int, float)):
            _cons_map[_k] = int(_v)
        elif isinstance(_v, dict):
            _cons_map[_k] = int(_v.get("level", 3))
    _rm_vals  = {int(k): v.get("value_COP", 0)
                 for k, v in _cfg_wb.get("risk_matrix", {})
                                     .get("consequence_levels", {}).items()
                 if not k.startswith("_")}
    _hz_bu    = {k: v for k, v in
                 _cfg_wb.get("asset_horizons_by_bu", {}).items()
                 if not k.startswith("_") and isinstance(v, dict)}

    # Shadow-price factor por BU — monetiza el CRA según el valor de la energía/agua
    # para cada negocio (normalizado a TyD = precio ENS XM ~335 k COP/MWh)
    _SP_BU = {
        "TyD":        335_000,    # Energía no suministrada — XM 2024
        "Generacion": 220_000,    # Contrato LP EPM
        "AyA":          3_100,    # Tarifa CRA 688 m³
        "Gas":        150_000,    # Precio gas industrial GNV
        "TI":          50_000,    # Costo hora parada sistemas críticos
        "Todos":       50_000,
    }

    _wbl_expert = st.session_state.get("expert_mode", False)

    # ── Controles — Fila 1: contexto del activo ──────────────────────────────
    _wc1, _wc2, _wc3, _wc4 = st.columns([1, 1, 1, 1], gap="medium")
    with _wc1:
        _wbl_bu = st.selectbox(
            "Unidad de Negocio", list(_hz_bu.keys()),
            key="wbl_bu",
            help="Define η (escala) desde asset_horizons_by_bu del config.json")
    with _wc2:
        _asset_classes = sorted(_beta_map.keys())
        _wbl_cls = st.selectbox("Clase de Activo", _asset_classes, key="wbl_cls")
    with _wc3:
        _wbl_inst = st.number_input(
            "Año de Instalación", 1960, 2024, 2000, step=1, key="wbl_inst")
    with _wc4:
        _wbl_cond = st.slider(
            "Condición actual (0=nuevo · 1=crítico)",
            0.0, 1.0, 0.40, 0.05, key="wbl_cond")

    # η vinculado a config.json: asset_horizons_by_bu → _eta_años bibliográfico → default 30a
    _bu_life_tbl = _hz_bu.get(_wbl_bu, {})
    _eta_from_bu  = _bu_life_tbl.get(_wbl_cls, _bu_life_tbl.get("_default", 0))
    _eta_from_bib = _eta_cfg_map.get(_wbl_cls, 0)
    _eta_cfg      = _eta_from_bu if _eta_from_bu > 0 else (_eta_from_bib if _eta_from_bib > 0 else 30)

    # ── Controles — Fila 2 (solo modo Experto) ────────────────────────────────
    if _wbl_expert:
        _we1, _we2, _we3 = st.columns([1, 1, 1], gap="medium")
        with _we1:
            _wbl_beta = st.slider(
                "β Weibull (shape)",
                0.5, 5.0, float(_beta_map.get(_wbl_cls, 2.5)), 0.1,
                key="wbl_beta",
                help="β<1 mortalidad infantil · β=1 falla aleatoria · β>1 desgaste")
        with _we2:
            _wbl_eta = st.number_input(
                "η Escala (Vida Útil años) — vinculado a config",
                5, 100, int(_eta_cfg), key="wbl_eta")
        with _we3:
            _wbl_cons = st.selectbox(
                "Nivel de Consecuencia L1–L5", [1, 2, 3, 4, 5],
                index=_cons_map.get(_wbl_cls, 3) - 1, key="wbl_cons")
    else:
        _wbl_beta = _beta_map.get(_wbl_cls, 2.5)
        _wbl_eta  = int(_eta_cfg)
        _wbl_cons = _cons_map.get(_wbl_cls, 3)

    # ── Cálculos ─────────────────────────────────────────────────────────────
    _wb_now_year  = datetime.now().year
    _wb_age_now   = _wb_now_year - int(_wbl_inst)
    _wb_plot_end  = max(60, _wbl_eta + 20)
    _wb_years_rel = list(range(0, _wb_plot_end + 1))

    def _wb_pof(age, beta, eta):
        if age <= 0 or eta <= 0:
            return 0.0
        return 1.0 - math.exp(-(max(age, 0) / eta) ** beta)

    def _wb_pof_lin(age, dl):
        return min(max(age, 0) / max(dl, 1), 1.0)

    # Anclar al current_condition: PoF(t_now) ≥ cond × 0.5
    _pof_curve_wb = []
    for _y in _wb_years_rel:
        _p = _wb_pof(_y, _wbl_beta, _wbl_eta)
        if _y == _wb_age_now:
            _p = max(_p, _wbl_cond * 0.5)
        _pof_curve_wb.append(_p)

    _pof_curve_li = [_wb_pof_lin(_y, _wbl_eta) for _y in _wb_years_rel]

    _wb_pof_now = max(_wb_pof(_wb_age_now, _wbl_beta, _wbl_eta),
                      _wbl_cond * 0.5)
    _wb_eeol_rel = next(
        (_y for _y, _p in zip(_wb_years_rel, _pof_curve_wb) if _p >= 0.50),
        _wbl_eta,
    )
    _wb_eeol_yr  = int(_wbl_inst) + _wb_eeol_rel

    # CRA = PoF × C_consecuencia × shadow_price_BU / 335_000 (normalizado)
    _cons_cop     = _rm_vals.get(_wbl_cons,
                    {1:80_000_000, 2:400_000_000, 3:1_000_000_000,
                     4:5_000_000_000, 5:25_000_000_000}.get(_wbl_cons, 1_000_000_000))
    _sp_factor    = _SP_BU.get(_wbl_bu, 335_000) / 335_000   # 1.0 = TyD
    _cra_cop      = _wb_pof_now * _cons_cop * _sp_factor
    _cra_mm       = _cra_cop / 1e6

    # ── KPI cards — fila compacta ─────────────────────────────────────────────
    _wk1, _wk2, _wk3, _wk4 = st.columns(4, gap="medium")
    with _wk1:
        metric_card("Edad del Activo", f"{_wb_age_now} años",
                    f"Instalado {int(_wbl_inst)} · η={_wbl_eta}a")
    with _wk2:
        _pof_lbl = ("🔴 CRÍTICO" if _wb_pof_now > 0.50
                    else "🟡 ALERTA" if _wb_pof_now > 0.25
                    else "🟢 NOMINAL")
        metric_card("PoF Actual", f"{_wb_pof_now*100:.1f}%", _pof_lbl,
                    EPM_RED if _wb_pof_now > 0.50
                    else EPM_AMBER if _wb_pof_now > 0.25 else EPM_GREEN)
    with _wk3:
        _yrs_to_eeol = max(0, _wb_eeol_yr - _wb_now_year)
        metric_card("EEOL estimado", str(_wb_eeol_yr),
                    f"En {_yrs_to_eeol} años (PoF 50%)",
                    EPM_RED if _yrs_to_eeol <= 3
                    else EPM_AMBER if _yrs_to_eeol <= 7 else EPM_TEAL)
    with _wk4:
        metric_card("CRA — Costo de Riesgo Anual",
                    f"{_cra_mm:,.1f} MM COP/año",
                    f"L{_wbl_cons} × SP_{_wbl_bu} ({_sp_factor:.2f}×TyD)",
                    EPM_RED if _cra_mm > 500 else EPM_AMBER)

    # ── Gráfico Weibull ───────────────────────────────────────────────────────
    _cal_years = [int(_wbl_inst) + _y for _y in _wb_years_rel]

    _fig_wb = go.Figure()
    # Zonas de riesgo (shading)
    _fig_wb.add_hrect(y0=0.00, y1=0.20,
        fillcolor="rgba(0,168,107,0.07)", line_width=0,
        annotation_text="Zona Nominal", annotation_position="top right",
        annotation_font=dict(size=9, color=EPM_GREEN))
    _fig_wb.add_hrect(y0=0.20, y1=0.50,
        fillcolor="rgba(217,119,6,0.09)", line_width=0,
        annotation_text="Zona Alerta", annotation_position="top right",
        annotation_font=dict(size=9, color=EPM_AMBER))
    _fig_wb.add_hrect(y0=0.50, y1=1.00,
        fillcolor="rgba(220,38,38,0.07)", line_width=0,
        annotation_text="Zona Crítica", annotation_position="top right",
        annotation_font=dict(size=9, color=EPM_RED))

    # Curva Weibull principal
    _fig_wb.add_trace(go.Scatter(
        x=_cal_years, y=_pof_curve_wb,
        mode="lines",
        name=f"Weibull  β={_wbl_beta:.1f}  η={_wbl_eta}a",
        line=dict(color=EPM_GREEN, width=2.5),
        hovertemplate="Año %{x} → PoF %{y:.1%}<extra></extra>",
    ))
    # Curva Lineal (siempre visible para comparación)
    _fig_wb.add_trace(go.Scatter(
        x=_cal_years, y=_pof_curve_li,
        mode="lines",
        name="Lineal (referencia)",
        line=dict(color=EPM_GRAY, width=1.5, dash="dash"),
        visible=True if _wbl_expert else "legendonly",
        hovertemplate="Año %{x} → PoF lineal %{y:.1%}<extra></extra>",
    ))
    # Marcador EEOL
    _fig_wb.add_vline(
        x=_wb_eeol_yr, line_dash="dot", line_color=EPM_RED,
        annotation_text=f"EEOL {_wb_eeol_yr}",
        annotation_font=dict(color=EPM_RED, size=10),
        annotation_position="top right",
    )
    # Marcador Año actual
    _fig_wb.add_vline(
        x=_wb_now_year, line_dash="solid", line_color=EPM_BLUE, line_width=1.5,
        annotation_text=f"Hoy {_wb_now_year}  PoF={_wb_pof_now:.0%}",
        annotation_font=dict(color=EPM_BLUE, size=10),
        annotation_position="top left",
    )
    _fig_wb.add_hline(
        y=0.50, line_dash="dot", line_color=EPM_RED, line_width=1,
    )
    # ── EEOL urgency badge ───────────────────────────────────────────────
    _yrs_to_eeol_wb = max(0, _wb_eeol_yr - _wb_now_year)
    _eeol_urgency   = (
        "🔴 URGENTE — EEOL en menos de 3 años" if _yrs_to_eeol_wb <= 3 else
        "🟡 ALERTA — EEOL en menos de 7 años"  if _yrs_to_eeol_wb <= 7 else
        "🟢 NOMINAL — Vida útil remanente suficiente"
    )
    _eeol_color = "#F87171" if _yrs_to_eeol_wb <= 3 else "#FCD34D" if _yrs_to_eeol_wb <= 7 else "#4ADE80"

    # Área sombreada Post-EEOL (zona de operación en riesgo extremo)
    _fig_wb.add_vrect(
        x0=_wb_eeol_yr, x1=int(_wbl_inst) + _wb_plot_end,
        fillcolor="rgba(220,38,38,0.06)", line_width=0,
        annotation_text="Operación post-EEOL",
        annotation_position="top left",
        annotation_font=dict(size=9, color="#F87171"),
    )

    _fig_wb.update_layout(
        height=440,
        margin=dict(t=40, b=70, l=70, r=30),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,0.6)",
        font=dict(family="Inter,Arial", size=11, color="#94A3B8"),
        yaxis=dict(
            title="Probabilidad de Falla PoF(t)",
            range=[0, 1],
            tickformat=".0%",
            gridcolor="rgba(255,255,255,0.05)",
            color="#94A3B8",
            title_font=dict(color="#94A3B8"),
        ),
        xaxis=dict(
            title="Año de Calendario",
            gridcolor="rgba(255,255,255,0.04)",
            color="#94A3B8",
            title_font=dict(color="#94A3B8"),
        ),
        legend=dict(
            orientation="h", y=-0.22, font=dict(size=10, color="#94A3B8"),
            bgcolor="rgba(15,23,42,0.6)", bordercolor="rgba(255,255,255,0.08)",
        ),
        showlegend=True,
        title=dict(
            text=f"Curva de Falla Weibull — {_wbl_cls} · {_eeol_urgency}",
            font=dict(size=12, color=_eeol_color),
            x=0.0, xanchor="left",
        ),
    )
    st.plotly_chart(_fig_wb, use_container_width=True)

    # ── Vista Experta: ecuación + tabla de puntos ─────────────────────────────
    if _wbl_expert:
        _exp_eq_col, _exp_tbl_col = st.columns([3, 2], gap="large")
        with _exp_eq_col:
            st.markdown(
                f'<div style="background:#0F172A;border:1px solid rgba(0,168,107,0.3);'
                f'border-radius:8px;padding:14px 20px;font-family:\'JetBrains Mono\','
                f'monospace;font-size:0.78rem;color:#E2E8F0;line-height:1.9">'
                f'<span style="color:#6EE7B7">PoF(t)</span>'
                f' = 1 − exp(−(t / η)^β)<br>'
                f'<span style="color:#94A3B8">β = {_wbl_beta:.2f} · '
                f'η = {_wbl_eta} a · t_now = {_wb_age_now} a</span><br>'
                f'<span style="color:#6EE7B7">CRA</span>'
                f' = PoF × C_L{_wbl_cons} × SP_{_wbl_bu}<br>'
                f'= {_wb_pof_now:.4f} × {_cons_cop/1e6:,.0f} MM × {_sp_factor:.2f}<br>'
                f'= <b style="color:#4ADE80">{_cra_mm:,.1f} MM COP/año</b><br>'
                f'<span style="color:#94A3B8;font-size:0.68rem">'
                f'η: asset_horizons_by_bu[{_wbl_bu}][{_wbl_cls}]={_eta_from_bu}a'
                + (f' · bib={_eta_from_bib:.0f}a' if _eta_from_bib > 0 else '')
                + f' → uso={_wbl_eta}a</span>'
                + (f'<br><span style="color:#6366F1;font-size:0.63rem">📚 {_fuente_map.get(_wbl_cls,"—")}</span>'
                   if _fuente_map.get(_wbl_cls) else '')
                + f'</div>',
                unsafe_allow_html=True)
        with _exp_tbl_col:
            _sample_ages = [0, 5, 10, 15, 20, 25, 30, 35, 40]
            _tbl_rows = []
            for _sa in _sample_ages:
                _p = _wb_pof(_sa, _wbl_beta, _wbl_eta)
                _r = _p * _cons_cop * _sp_factor / 1e6
                _tbl_rows.append({
                    "Año": int(_wbl_inst) + _sa,
                    "Edad": _sa,
                    "PoF": f"{_p:.1%}",
                    "CRA (MM/año)": f"{_r:,.1f}",
                })
            st.dataframe(
                pd.DataFrame(_tbl_rows),
                use_container_width=True,
                hide_index=True,
                height=240,
            )

    # ── Vista de Flota — Ranking de clases por CRA (BU seleccionada) ─────────
    with st.expander("🏭 Vista de Flota — CRA por clase de activo en la BU seleccionada"):
        st.caption(
            "Supuesto: cada clase tiene 1 activo con edad media = η/2 y condición neutral. "
            "Útil para identificar qué clases concentran el mayor riesgo de la BU."
        )
        _fleet_rows = []
        for _fc, _fb in sorted(_beta_map.items()):
            # Filtrar a la BU seleccionada: la clase debe existir en asset_horizons_by_bu
            _fb_life  = _hz_bu.get(_wbl_bu, {}).get(_fc, 0)
            _fb_eta_bib = _eta_cfg_map.get(_fc, 0)
            _fb_eta   = _fb_life if _fb_life > 0 else (_fb_eta_bib if _fb_eta_bib > 0 else 0)
            if _fb_eta == 0:
                continue   # clase no pertenece a esta BU
            _fb_age   = _fb_eta / 2.0   # edad media asumida
            _fb_pof   = _wb_pof(_fb_age, _fb, _fb_eta)
            _fb_cons  = _cons_map.get(_fc, 3)
            _fb_cop   = _rm_vals.get(_fb_cons,
                        {1:80_000_000, 2:400_000_000, 3:1_000_000_000,
                         4:5_000_000_000, 5:25_000_000_000}.get(_fb_cons, 1_000_000_000))
            _fb_sp    = _SP_BU.get(_wbl_bu, 335_000) / 335_000
            _fb_cra   = _fb_pof * _fb_cop * _fb_sp / 1e6
            _fleet_rows.append({
                "Clase": _fc,
                "β": f"{_fb:.1f}",
                "η (a)": int(_fb_eta),
                "Edad media (a)": f"{_fb_age:.0f}",
                "PoF@media": f"{_fb_pof:.1%}",
                "L-Consecuencia": f"L{_fb_cons}",
                "CRA (MM COP/año)": round(_fb_cra, 1),
            })
        if _fleet_rows:
            _fleet_df = pd.DataFrame(sorted(_fleet_rows,
                                            key=lambda r: r["CRA (MM COP/año)"],
                                            reverse=True))
            def _cra_style(v):
                if isinstance(v, (int, float)):
                    if v > 500:   return "background:#450a0a;color:#FCA5A5"
                    if v > 100:   return "background:#451a03;color:#FCD34D"
                    return "background:#052e16;color:#6EE7B7"
                return ""
            st.dataframe(
                _style_apply(
                    _fleet_df.style.format({"CRA (MM COP/año)": "{:,.1f}"}),
                    _cra_style, subset=["CRA (MM COP/año)"]
                ),
                use_container_width=True,
                hide_index=True,
                height=min(38 * len(_fleet_rows) + 45, 400),
            )
        else:
            st.info(f"No hay clases de activo configuradas para {_wbl_bu} en asset_horizons_by_bu.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB AUDITORÍA — §3 Capacity Gap & Shadow Price Monetization  (Módulo 3)
# ══════════════════════════════════════════════════════════════════════════════
with _tab_audi:
    st.divider()
    page_header(
        "Análisis de Restricciones — Capacity Gap",
        "Demanda vs Oferta · Shadow Price · VNT No Capturado · Monetización del Gap",
        "Módulo Enterprise 3 · MILP Shadow Prices",
    )

    _cg_cfg = get_config()
    _cg_wacc = _cg_cfg.get("wacc", 0.10)
    _cg_hz   = _cg_cfg.get("analysis_horizon_years", 10)

    # ── Controles del Capacity Gap ────────────────────────────────────────────
    _cg_c1, _cg_c2, _cg_c3, _cg_c4 = st.columns(4, gap="medium")
    with _cg_c1:
        _cg_bu = st.selectbox(
            "Negocio", ["TyD", "AyA", "Gas", "Generacion"],
            key="cg_bu",
            help="Define el shadow price unitario del gap (COP por unidad de capacidad)")
    with _cg_c2:
        _cg_cap_now = st.number_input(
            "Capacidad actual (unidades)", min_value=1.0,
            value=100.0, step=10.0, key="cg_cap_now",
            help="Capacidad instalada hoy — MW, m³/h, m³/día, conexiones, etc.")
    with _cg_c3:
        _cg_demand_yr1 = st.number_input(
            "Demanda Año 1 (unidades)", min_value=1.0,
            value=90.0, step=5.0, key="cg_demand_yr1")
    with _cg_c4:
        _cg_demand_growth = st.slider(
            "Crecimiento demanda (%/año)", 0.0, 20.0, 5.0, 0.5, key="cg_growth",
            help="Tasa de crecimiento anual de la demanda")

    _cg_c5, _cg_c6, _cg_c7 = st.columns(3, gap="medium")
    with _cg_c5:
        _cg_invest_cap = st.number_input(
            "Capacidad que agrega la inversión (unidades)", min_value=0.0,
            value=50.0, step=10.0, key="cg_invest_cap",
            help="Capacidad adicional que aporta el proyecto bajo evaluación")
    with _cg_c6:
        _cg_invest_yr  = st.selectbox(
            "Año de entrada en servicio", list(range(2025, 2036)),
            key="cg_invest_yr")
    with _cg_c7:
        # Shadow price unitario por BU
        _SP_GAP = {
            "TyD":        335_000,    # COP/MWh ENS
            "AyA":          3_100,    # COP/m³ agua no suministrada
            "Gas":        150_000,    # COP/m³ gas
            "Generacion": 355_000,    # COP/MWh generación
        }
        _sp_default = _SP_GAP.get(_cg_bu, 335_000)
        _cg_sp = st.number_input(
            "Shadow Price (COP/unidad-año)", min_value=0.0,
            value=float(_sp_default), step=1_000.0, key="cg_sp",
            help="Costo de oportunidad por cada unidad de capacidad no satisfecha por año")

    # ── Proyecciones ─────────────────────────────────────────────────────────
    _cg_years    = list(range(2025, 2025 + _cg_hz + 1))
    _cg_supply   = []  # Capacidad disponible
    _cg_demand   = []  # Demanda proyectada
    _cg_gap      = []  # Gap (demanda − oferta, ≥0)
    _cg_shadow   = []  # Shadow price monetizado del gap (MM COP/año)

    for _yi, _yr in enumerate(_cg_years):
        _dem = _cg_demand_yr1 * (1 + _cg_demand_growth / 100) ** _yi
        _sup = _cg_cap_now
        if _yr >= _cg_invest_yr:
            _sup += _cg_invest_cap
        _gap_val = max(0.0, _dem - _sup)
        _cg_supply.append(_sup)
        _cg_demand.append(_dem)
        _cg_gap.append(_gap_val)
        _cg_shadow.append(_gap_val * _cg_sp / 1_000_000)  # MM COP

    # VNT No Capturado = shadow price descontado del gap sin intervención
    _cg_gap_no_inv = []
    for _yi, _yr in enumerate(_cg_years):
        _dem = _cg_demand_yr1 * (1 + _cg_demand_growth / 100) ** _yi
        _gap_val = max(0.0, _dem - _cg_cap_now)  # sin inversión
        _cg_gap_no_inv.append(_gap_val * _cg_sp / 1_000_000)

    _cg_fpv_vec = [(1 / (1 + _cg_wacc) ** _yi) for _yi in range(len(_cg_years))]
    _vnt_no_cap_mm   = sum(g * f for g, f in zip(_cg_gap_no_inv, _cg_fpv_vec))
    _vnt_cap_mm      = sum(g * f for g, f in zip(_cg_shadow,     _cg_fpv_vec))
    _vnt_no_cap_gap  = _vnt_no_cap_mm - _vnt_cap_mm  # VNT recuperado por la inversión

    # ── KPIs Capacity Gap ─────────────────────────────────────────────────────
    _cgk1, _cgk2, _cgk3, _cgk4 = st.columns(4, gap="medium")
    _gap_yr1 = max(0, _cg_demand_yr1 - _cg_cap_now)
    _gap_yr_cross = next(
        (_yr for _yr, _d in zip(_cg_years, _cg_demand) if _d > _cg_cap_now), None)
    _gap_yr_cross_inv = next(
        (_yr for _yr, _d, _s in zip(_cg_years, _cg_demand, _cg_supply) if _d > _s), None)

    with _cgk1:
        metric_card(
            "Gap Año 1", f"{_gap_yr1:.1f} unidades",
            "Diferencia demanda − capacidad actual",
            EPM_RED if _gap_yr1 > 0 else EPM_GREEN)
    with _cgk2:
        metric_card(
            "Año de Quiebre (sin inv.)",
            str(_gap_yr_cross) if _gap_yr_cross else "No cruza",
            "Cuando demanda supera capacidad actual",
            EPM_AMBER if _gap_yr_cross else EPM_GREEN)
    with _cgk3:
        metric_card(
            "VNT No Capturado (sin inv.)",
            f"{_vnt_no_cap_mm:,.1f} MM COP",
            "Shadow price del gap total descontado",
            EPM_RED if _vnt_no_cap_mm > 0 else EPM_GREEN)
    with _cgk4:
        metric_card(
            "VNT Recuperado por Inversión",
            f"{_vnt_no_cap_gap:,.1f} MM COP",
            "Reducción del shadow price con el proyecto",
            EPM_GREEN if _vnt_no_cap_gap > 0 else EPM_AMBER)

    # ── Gráfico Capacity Gap ─────────────────────────────────────────────────
    _fig_cg = go.Figure()

    # Área de brecha sin inversión (gap no capturado)
    _cg_shadow_no_inv = [_g * _cg_sp / 1_000_000 for _g in [
        max(0, _cg_demand_yr1 * (1 + _cg_demand_growth / 100) ** _yi - _cg_cap_now)
        for _yi in range(len(_cg_years))
    ]]

    _fig_cg.add_trace(go.Scatter(
        x=_cg_years, y=_cg_demand,
        mode="lines", name="Demanda Proyectada",
        line=dict(color="#F87171", width=2.5, dash="solid"),
        hovertemplate="Año %{x} → Demanda %{y:.1f} un.<extra></extra>",
    ))
    _fig_cg.add_trace(go.Scatter(
        x=_cg_years, y=[_cg_cap_now] * len(_cg_years),
        mode="lines", name="Capacidad actual (sin inv.)",
        line=dict(color="#94A3B8", width=1.5, dash="dot"),
        hovertemplate="Año %{x} → Capacidad sin inversión %{y:.1f} un.<extra></extra>",
    ))
    _fig_cg.add_trace(go.Scatter(
        x=_cg_years, y=_cg_supply,
        mode="lines", name=f"Capacidad con inversión ({_cg_invest_yr})",
        line=dict(color="#00A86B", width=2.5),
        fill="tonexty",
        fillcolor="rgba(0,168,107,0.08)",
        hovertemplate="Año %{x} → Capacidad con inversión %{y:.1f} un.<extra></extra>",
    ))

    # Área de gap monetizado (shadow price)
    _fig_cg.add_trace(go.Bar(
        x=_cg_years,
        y=_cg_shadow_no_inv,
        name="VNT No Capturado (MM COP/año)",
        marker_color="rgba(220,38,38,0.35)",
        marker_line_color="rgba(220,38,38,0.6)",
        marker_line_width=1,
        yaxis="y2",
        hovertemplate="Año %{x} → Shadow %{y:,.1f} MM COP/año<extra></extra>",
    ))
    _fig_cg.add_trace(go.Bar(
        x=_cg_years,
        y=_cg_shadow,
        name="Shadow Price residual (con inv., MM COP/año)",
        marker_color="rgba(217,119,6,0.25)",
        marker_line_color="rgba(217,119,6,0.5)",
        marker_line_width=1,
        yaxis="y2",
        hovertemplate="Año %{x} → Shadow residual %{y:,.1f} MM COP/año<extra></extra>",
    ))

    # Línea vertical año de inversión
    if _cg_invest_yr in _cg_years:
        _fig_cg.add_vline(
            x=_cg_invest_yr, line_dash="solid", line_color="#00A86B", line_width=2,
            annotation_text=f"Entrada servicio {_cg_invest_yr}",
            annotation_font=dict(color="#6EE7B7", size=10),
            annotation_position="top left",
        )

    _fig_cg.update_layout(
        height=440,
        margin=dict(t=50, b=70, l=70, r=80),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,0.6)",
        barmode="overlay",
        font=dict(family="Inter,Arial", size=11, color="#94A3B8"),
        title=dict(
            text=(f"Demanda vs Oferta — {_cg_bu}  |  "
                  f"VNT No Capturado (sin inv.): ${_vnt_no_cap_mm:,.0f} MM COP  |  "
                  f"Recuperado: ${_vnt_no_cap_gap:,.0f} MM COP"),
            font=dict(size=11, color="#CBD5E1"), x=0, xanchor="left",
        ),
        yaxis=dict(
            title="Capacidad / Demanda (unidades)",
            gridcolor="rgba(255,255,255,0.05)", color="#94A3B8",
            title_font=dict(color="#94A3B8"),
        ),
        yaxis2=dict(
            title="Shadow Price (MM COP/año)",
            overlaying="y", side="right",
            gridcolor="rgba(0,0,0,0)", color="#F87171",
            title_font=dict(color="#F87171"),
        ),
        xaxis=dict(
            title="Año", gridcolor="rgba(255,255,255,0.04)", color="#94A3B8",
            title_font=dict(color="#94A3B8"),
        ),
        legend=dict(
            orientation="h", y=-0.2, font=dict(size=10, color="#94A3B8"),
            bgcolor="rgba(15,23,42,0.6)", bordercolor="rgba(255,255,255,0.08)",
        ),
    )
    st.plotly_chart(_fig_cg, use_container_width=True)

    # ── Tabla de shadow prices año a año ─────────────────────────────────────
    if not st.session_state.get("executive_mode", False):
        with st.expander("📋 Tabla detallada — Shadow Prices año a año"):
            _cg_tbl = []
            for _yi, _yr in enumerate(_cg_years):
                _cg_tbl.append({
                    "Año": _yr,
                    "Demanda (un.)": f"{_cg_demand[_yi]:.1f}",
                    "Oferta sin inv. (un.)": f"{_cg_cap_now:.1f}",
                    "Oferta con inv. (un.)": f"{_cg_supply[_yi]:.1f}",
                    "Gap sin inv. (un.)": f"{max(0, _cg_demand[_yi]-_cg_cap_now):.1f}",
                    "Gap con inv. (un.)": f"{_cg_gap[_yi]:.1f}",
                    "Shadow sin inv. (MM/año)": f"{_cg_shadow_no_inv[_yi]:,.2f}",
                    "Shadow con inv. (MM/año)": f"{_cg_shadow[_yi]:,.2f}",
                    "FVP": f"{_cg_fpv_vec[_yi]:.4f}",
                })
            st.dataframe(pd.DataFrame(_cg_tbl), use_container_width=True, hide_index=True)

    st.markdown(
        f'<div class="box-info" style="margin-top:12px">'
        f'💡 <b>Interpretación:</b> Con inversión en {_cg_invest_yr}, '
        f'el VNT No Capturado se reduce de <b>${_vnt_no_cap_mm:,.0f} MM</b> '
        f'a <b>${_vnt_cap_mm:,.0f} MM</b> COP (VPN a WACC {_cg_wacc*100:.0f}%). '
        f'La diferencia <b>${_vnt_no_cap_gap:,.0f} MM</b> es el valor adicional '
        f'que este proyecto aporta por eliminar el gap de capacidad — '
        f'componente que complementa el VNT del Wizard de Evaluación.</div>',
        unsafe_allow_html=True)

    # ── Análisis de sensibilidad: crecimiento × año de inversión ─────────────
    if not st.session_state.get("executive_mode", False):
        with st.expander("🔬 Sensibilidad — VNT Recuperado: Crecimiento × Año de Inversión"):
            st.caption(
                "Cada celda muestra el VNT Recuperado (MM COP) según la tasa de crecimiento anual "
                "y el año de entrada en servicio de la inversión. Verde = mayor recuperación."
            )
            _sg_rows   = [2.0, 3.5, 5.0, 7.5, 10.0, 15.0]   # growth %/año
            _sg_yrs    = [2025, 2026, 2027, 2028, 2029, 2030]  # año entrada
            _sg_matrix = []
            for _sgr in _sg_rows:
                _sg_row_vals = []
                for _sgy in _sg_yrs:
                    # calcular VNT recuperado para esta combinación
                    _sg_gap_ni = sum(
                        max(0, _cg_demand_yr1 * (1 + _sgr/100)**_yi - _cg_cap_now)
                        * _cg_sp / 1_000_000 / (1 + _cg_wacc)**_yi
                        for _yi in range(len(_cg_years))
                    )
                    _sg_sup2   = [
                        _cg_cap_now + (_cg_invest_cap if _yr >= _sgy else 0)
                        for _yi, _yr in enumerate(_cg_years)
                    ]
                    _sg_gap_inv = sum(
                        max(0, _cg_demand_yr1 * (1 + _sgr/100)**_yi - _sg_sup2[_yi])
                        * _cg_sp / 1_000_000 / (1 + _cg_wacc)**_yi
                        for _yi in range(len(_cg_years))
                    )
                    _sg_row_vals.append(round(_sg_gap_ni - _sg_gap_inv, 0))
                _sg_matrix.append(_sg_row_vals)

            _sg_df = pd.DataFrame(
                _sg_matrix,
                index=[f"{g:.1f}%/año" for g in _sg_rows],
                columns=[str(y) for y in _sg_yrs],
            )
            _sg_max = max(abs(_sg_df.values.max()), 1)

            def _sg_heat(v):
                if not isinstance(v, (int, float)):
                    return ""
                ratio = v / _sg_max
                if ratio > 0.7:   return "background:#052e16;color:#4ADE80;font-weight:700"
                if ratio > 0.4:   return "background:#0D3321;color:#86EFAC"
                if ratio > 0.1:   return "background:#0A2518;color:#A7F3D0"
                return "background:#1E293B;color:#94A3B8"

            st.dataframe(
                _style_apply(
                    _sg_df.style.format("{:,.0f}"),
                    _sg_heat
                ),
                use_container_width=True,
            )
            st.caption(
                "📌 Cuanto mayor el crecimiento de demanda y más temprana la inversión, "
                "mayor el VNT Recuperado. El shadow price actual "
                f"({_cg_bu}: {_cg_sp:,.0f} COP/unidad-año) es el multiplicador clave."
            )

# ══════════════════════════════════════════════════════════════════════════════
# TAB AUDITORÍA — §4 Scenario Duel — Comparación Cara a Cara de Portafolios
# ══════════════════════════════════════════════════════════════════════════════
with _tab_audi:
    st.divider()
    page_header(
        "Duelo de Escenarios — Comparación Cara a Cara",
        "Selecciona 2 escenarios guardados · VNT · CAPEX · Proyectos · Radar de Valor",
        "Módulo Enterprise 4 · C55 §7.2 Scenario Comparison · ISO 55001",
    )

    _duel_saved = st.session_state.get("saved_scenarios", {})
    _duel_names = list(_duel_saved.keys())

    if len(_duel_names) < 2:
        st.info(
            "Necesitas al menos **2 escenarios guardados** para activar el Duelo. "
            "Ve a la pestaña **Planeador → Optimizador**, ejecuta la optimización con "
            "diferentes parámetros y guarda cada resultado como un escenario nombrado."
        )
    else:
        # ── Selector de los 2 escenarios a duelo ─────────────────────────────
        _d_col1, _d_col2 = st.columns(2, gap="large")
        with _d_col1:
            _duel_a = st.selectbox(
                "Escenario A",
                _duel_names,
                index=0,
                key="duel_a",
                help="Primer portafolio para comparar — aparece en verde")
        with _d_col2:
            _duel_b_opts = [n for n in _duel_names if n != _duel_a]
            _duel_b = st.selectbox(
                "Escenario B",
                _duel_b_opts,
                index=0,
                key="duel_b",
                help="Segundo portafolio para comparar — aparece en ámbar")

        _da = _duel_saved[_duel_a]
        _db = _duel_saved[_duel_b]

        # ── KPI Cards lado a lado ─────────────────────────────────────────────
        _dk_labels  = ["VNT Total", "CAPEX Total", "Proyectos sel.", "VNT/CAPEX"]
        _dk_vals_a  = [
            f"{_da['vnt_COP']/1e9:.3f} B COP",
            f"{_da['capex_COP']/1e6:,.0f} MM COP",
            str(_da["n_proj"]),
            f"{_da['vnt_COP']/max(_da['capex_COP'],1):.3f}×",
        ]
        _dk_vals_b  = [
            f"{_db['vnt_COP']/1e9:.3f} B COP",
            f"{_db['capex_COP']/1e6:,.0f} MM COP",
            str(_db["n_proj"]),
            f"{_db['vnt_COP']/max(_db['capex_COP'],1):.3f}×",
        ]
        # Deltas numéricos
        _dk_delta   = [
            _da['vnt_COP']  - _db['vnt_COP'],
            _da['capex_COP']- _db['capex_COP'],
            _da['n_proj']   - _db['n_proj'],
            (_da['vnt_COP']/max(_da['capex_COP'],1)) - (_db['vnt_COP']/max(_db['capex_COP'],1)),
        ]
        _dk_delta_fmt = [
            f"{_dk_delta[0]/1e9:+.3f} B",
            f"{_dk_delta[1]/1e6:+,.0f} MM",
            f"{_dk_delta[2]:+d}",
            f"{_dk_delta[3]:+.3f}×",
        ]
        _dk_higher_a_is_better = [True, False, True, True]

        _kpi_row = st.columns(4, gap="medium")
        for _ki in range(4):
            _higher_a = _dk_delta[_ki] > 0
            _neutral  = abs(_dk_delta[_ki]) < 1e-9
            _a_wins   = (_higher_a and _dk_higher_a_is_better[_ki]) or \
                        (not _higher_a and not _dk_higher_a_is_better[_ki])
            _badge_col = EPM_GREEN if (_neutral or _a_wins) else EPM_AMBER
            with _kpi_row[_ki]:
                st.markdown(
                    f'<div style="background:rgba(15,23,42,0.7);border:1px solid rgba(255,255,255,0.08);'
                    f'border-radius:10px;padding:14px 16px;text-align:center">'
                    f'<div style="font-size:0.62rem;font-weight:700;color:#6EE7B7;'
                    f'text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px">'
                    f'{_dk_labels[_ki]}</div>'
                    f'<div style="display:flex;justify-content:space-around;align-items:center;gap:8px">'
                    f'<div style="text-align:center">'
                    f'<div style="font-size:0.6rem;color:{EPM_GREEN};font-weight:700">A</div>'
                    f'<div style="font-size:0.88rem;font-weight:800;color:#ECFDF5">{_dk_vals_a[_ki]}</div>'
                    f'</div>'
                    f'<div style="font-size:0.75rem;color:{_badge_col};font-weight:700;'
                    f'background:rgba(255,255,255,0.05);padding:2px 8px;border-radius:6px">'
                    f'{_dk_delta_fmt[_ki]}</div>'
                    f'<div style="text-align:center">'
                    f'<div style="font-size:0.6rem;color:{EPM_AMBER};font-weight:700">B</div>'
                    f'<div style="font-size:0.88rem;font-weight:800;color:#ECFDF5">{_dk_vals_b[_ki]}</div>'
                    f'</div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Layout principal: Radar + CAPEX por año ────────────────────────────
        _radar_col, _capex_col = st.columns([1, 1], gap="large")

        with _radar_col:
            st.markdown("##### Radar de Valor — A vs B")
            # Normalizar métricas [0–1] para radar
            _max_vnt    = max(_da["vnt_COP"],    _db["vnt_COP"],  1)
            _max_capex  = max(_da["capex_COP"],  _db["capex_COP"], 1)
            _max_proj   = max(_da["n_proj"],     _db["n_proj"],    1)
            _max_ratio  = max(_da["vnt_COP"]/max(_da["capex_COP"],1),
                              _db["vnt_COP"]/max(_db["capex_COP"],1), 1e-9)
            _max_post   = max(_da.get("n_postponed",0), _db.get("n_postponed",0), 1)
            # G5: _max_time era dead code (no usada en radar) — eliminada

            _radar_cats = ["VNT\n(mayor=mejor)", "CAPEX\n(menor=mejor)", "Proyectos\nsel.",
                           "VNT/CAPEX\n(mayor=mejor)", "Postergados\n(menor=mejor)"]
            # Invertir métricas donde "menor es mejor"
            _r_a = [
                _da["vnt_COP"] / _max_vnt,
                1 - _da["capex_COP"] / _max_capex,
                _da["n_proj"] / _max_proj,
                (_da["vnt_COP"]/max(_da["capex_COP"],1)) / _max_ratio,
                1 - _da.get("n_postponed",0) / _max_post,
            ]
            _r_b = [
                _db["vnt_COP"] / _max_vnt,
                1 - _db["capex_COP"] / _max_capex,
                _db["n_proj"] / _max_proj,
                (_db["vnt_COP"]/max(_db["capex_COP"],1)) / _max_ratio,
                1 - _db.get("n_postponed",0) / _max_post,
            ]
            # Cerrar polígono
            _rc  = _radar_cats + [_radar_cats[0]]
            _r_a = _r_a + [_r_a[0]]
            _r_b = _r_b + [_r_b[0]]

            _fig_radar = go.Figure()
            _fig_radar.add_trace(go.Scatterpolar(
                r=_r_a, theta=_rc,
                fill="toself", fillcolor=f"rgba(0,168,107,0.18)",
                line=dict(color=EPM_GREEN, width=2),
                name=_duel_a[:24],
                hovertemplate="%{theta}<br>Score: %{r:.0%}<extra>A</extra>",
            ))
            _fig_radar.add_trace(go.Scatterpolar(
                r=_r_b, theta=_rc,
                fill="toself", fillcolor=f"rgba(217,119,6,0.15)",
                line=dict(color=EPM_AMBER, width=2, dash="dot"),
                name=_duel_b[:24],
                hovertemplate="%{theta}<br>Score: %{r:.0%}<extra>B</extra>",
            ))
            _fig_radar.update_layout(
                height=360,
                margin=dict(t=30, b=30, l=40, r=40),
                paper_bgcolor="rgba(0,0,0,0)",
                polar=dict(
                    bgcolor="rgba(15,23,42,0.55)",
                    radialaxis=dict(
                        visible=True, range=[0, 1],
                        tickformat=".0%",
                        gridcolor="rgba(255,255,255,0.08)",
                        color="#94A3B8",
                    ),
                    angularaxis=dict(
                        gridcolor="rgba(255,255,255,0.06)",
                        color="#94A3B8",
                    ),
                ),
                legend=dict(
                    orientation="h", y=-0.12,
                    bgcolor="rgba(15,23,42,0.6)",
                    font=dict(size=11, color="#CBD5E1"),
                ),
            )
            st.plotly_chart(_fig_radar, use_container_width=True)

        with _capex_col:
            st.markdown("##### CAPEX comprometido por vigencia")
            _duel_yr_union = sorted(set(
                yr for _dd in [_da, _db]
                for yr in _dd.get("capex_by_yr", {}).keys()
            ))
            if _duel_yr_union:
                _fig_duel_capex = go.Figure()
                _fig_duel_capex.add_trace(go.Bar(
                    x=[str(y) for y in _duel_yr_union],
                    y=[_da.get("capex_by_yr", {}).get(yr, 0) / 1e6 for yr in _duel_yr_union],
                    name=f"A: {_duel_a[:20]}",
                    marker_color=EPM_GREEN,
                    opacity=0.85,
                    hovertemplate="Año %{x}<br>A: %{y:,.0f} MM COP<extra></extra>",
                ))
                _fig_duel_capex.add_trace(go.Bar(
                    x=[str(y) for y in _duel_yr_union],
                    y=[_db.get("capex_by_yr", {}).get(yr, 0) / 1e6 for yr in _duel_yr_union],
                    name=f"B: {_duel_b[:20]}",
                    marker_color=EPM_AMBER,
                    opacity=0.85,
                    hovertemplate="Año %{x}<br>B: %{y:,.0f} MM COP<extra></extra>",
                ))
                _fig_duel_capex.update_layout(
                    barmode="group",
                    height=360,
                    xaxis_title="Vigencia",
                    yaxis_title="CAPEX (MM COP)",
                    margin=dict(t=10, b=50, l=60, r=20),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(15,23,42,0.55)",
                    yaxis=dict(gridcolor="rgba(255,255,255,0.05)", color="#94A3B8"),
                    xaxis=dict(gridcolor="rgba(255,255,255,0.04)", color="#94A3B8"),
                    legend=dict(
                        orientation="h", y=-0.22,
                        bgcolor="rgba(15,23,42,0.6)", font=dict(size=10, color="#CBD5E1"),
                    ),
                )
                st.plotly_chart(_fig_duel_capex, use_container_width=True)
            else:
                st.info("Los escenarios guardados no contienen desglose por vigencia.")

        # ── Diff de proyectos ─────────────────────────────────────────────────
        st.markdown("##### Diferencial de Proyectos")
        _ids_a   = set(_da.get("selected_ids", []))
        _ids_b   = set(_db.get("selected_ids", []))
        _only_a  = sorted(_ids_a - _ids_b)
        _only_b  = sorted(_ids_b - _ids_a)
        _common  = sorted(_ids_a & _ids_b)

        _diff_c1, _diff_c2, _diff_c3 = st.columns(3, gap="medium")
        with _diff_c1:
            st.markdown(
                f'<div style="background:rgba(0,168,107,0.10);border:1px solid rgba(0,168,107,0.3);'
                f'border-radius:8px;padding:12px 14px">'
                f'<div style="font-size:0.62rem;font-weight:700;color:{EPM_GREEN};'
                f'text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px">'
                f'Solo en A — {len(_only_a)} proyectos</div>'
                + "".join(
                    f'<div style="font-size:0.78rem;color:#A7F3D0;padding:2px 0;'
                    f'border-bottom:1px solid rgba(255,255,255,0.04)">{pid}</div>'
                    for pid in (_only_a or ["(ninguno)"])
                )
                + f'</div>',
                unsafe_allow_html=True)
        with _diff_c2:
            st.markdown(
                f'<div style="background:rgba(217,119,6,0.10);border:1px solid rgba(217,119,6,0.3);'
                f'border-radius:8px;padding:12px 14px">'
                f'<div style="font-size:0.62rem;font-weight:700;color:{EPM_AMBER};'
                f'text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px">'
                f'Solo en B — {len(_only_b)} proyectos</div>'
                + "".join(
                    f'<div style="font-size:0.78rem;color:#FCD34D;padding:2px 0;'
                    f'border-bottom:1px solid rgba(255,255,255,0.04)">{pid}</div>'
                    for pid in (_only_b or ["(ninguno)"])
                )
                + f'</div>',
                unsafe_allow_html=True)
        with _diff_c3:
            st.markdown(
                f'<div style="background:rgba(30,64,175,0.10);border:1px solid rgba(30,64,175,0.3);'
                f'border-radius:8px;padding:12px 14px">'
                f'<div style="font-size:0.62rem;font-weight:700;color:{EPM_BLUE};'
                f'text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px">'
                f'Comunes — {len(_common)} proyectos</div>'
                + "".join(
                    f'<div style="font-size:0.78rem;color:#93C5FD;padding:2px 0;'
                    f'border-bottom:1px solid rgba(255,255,255,0.04)">{pid}</div>'
                    for pid in (_common or ["(ninguno)"])
                )
                + f'</div>',
                unsafe_allow_html=True)

        # ── Veredicto ─────────────────────────────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        # Score: suma ponderada de las métricas normalizadas del radar
        _score_a = sum(_r_a[:-1]) / len(_r_a[:-1])
        _score_b = sum(_r_b[:-1]) / len(_r_b[:-1])
        _winner_name = _duel_a if _score_a >= _score_b else _duel_b
        _winner_score = max(_score_a, _score_b)
        _loser_score  = min(_score_a, _score_b)
        _score_diff   = _winner_score - _loser_score
        _verdict_str  = (
            "Empate técnico — ambos escenarios son equivalentes" if _score_diff < 0.02 else
            f"Ventaja clara de {_winner_name}" if _score_diff > 0.15 else
            f"Leve ventaja de {_winner_name}"
        )
        _verdict_color = EPM_AMBER if _score_diff < 0.02 else EPM_GREEN
        st.markdown(
            f'<div class="box-info" style="margin-top:4px;border-color:{_verdict_color}30">'
            f'<b style="color:{_verdict_color}">Veredicto:</b> {_verdict_str}. '
            f'Score radar A = {_score_a:.1%} · B = {_score_b:.1%}. '
            f'A tiene {len(_only_a)} proyectos únicos y B tiene {len(_only_b)} únicos; '
            f'{len(_common)} proyectos son comunes a ambos portafolios.</div>',
            unsafe_allow_html=True)

        # ── Desglose VNT por proyecto: waterfall lado a lado ─────────────────
        _da_detail = _da.get("projects_detail", [])
        _db_detail = _db.get("projects_detail", [])

        if _da_detail or _db_detail:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("##### Composición del VNT — Proyectos A vs B")
            st.caption(
                "Cada barra representa el VNT neto (MM COP) aportado por un proyecto. "
                "Proyectos exclusivos de cada escenario aparecen con valor 0 en el otro. "
                "Ordenados por VNT-A descendente."
            )

            # Mapa de proyectos por ID
            _dp_map_a = {p["id"]: p for p in _da_detail}
            _dp_map_b = {p["id"]: p for p in _db_detail}
            _dp_all_ids = sorted(
                set(_dp_map_a.keys()) | set(_dp_map_b.keys()),
                key=lambda pid: -_dp_map_a.get(pid, {}).get("vnt_COP", 0),
            )

            _dp_vnt_a = [_dp_map_a.get(pid, {}).get("vnt_COP", 0) / 1e6 for pid in _dp_all_ids]
            _dp_vnt_b = [_dp_map_b.get(pid, {}).get("vnt_COP", 0) / 1e6 for pid in _dp_all_ids]
            _dp_labels = [
                (_dp_map_a.get(pid) or _dp_map_b.get(pid, {})).get("name", pid)[:22]
                for pid in _dp_all_ids
            ]

            _fig_dp = go.Figure()
            _fig_dp.add_trace(go.Bar(
                x=_dp_labels,
                y=_dp_vnt_a,
                name=f"A: {_duel_a[:22]}",
                marker_color=[EPM_GREEN if v >= 0 else EPM_RED for v in _dp_vnt_a],
                opacity=0.88,
                hovertemplate="%{x}<br>VNT-A: <b>%{y:+,.0f} MM COP</b><extra>Escenario A</extra>",
            ))
            _fig_dp.add_trace(go.Bar(
                x=_dp_labels,
                y=_dp_vnt_b,
                name=f"B: {_duel_b[:22]}",
                marker_color=[EPM_AMBER if v >= 0 else "#7C3AED" for v in _dp_vnt_b],
                opacity=0.88,
                hovertemplate="%{x}<br>VNT-B: <b>%{y:+,.0f} MM COP</b><extra>Escenario B</extra>",
            ))
            # Línea cero
            _fig_dp.add_hline(y=0, line=dict(color="rgba(255,255,255,0.2)", width=1))
            _fig_dp.update_layout(
                barmode="group",
                height=min(400, max(260, 36 * len(_dp_all_ids) + 80)),
                margin=dict(t=10, b=60, l=70, r=20),
                xaxis=dict(title="Proyecto", tickangle=-35, tickfont=dict(size=9)),
                yaxis=dict(title="VNT (MM COP)", gridcolor="rgba(255,255,255,0.06)"),
                legend=dict(orientation="h", y=-0.28, font=dict(size=10)),
            )
            st.plotly_chart(_fig_dp, use_container_width=True,
                            config={"displayModeBar": False})

            # Tabla de diferencial
            _dp_deltas = [a - b for a, b in zip(_dp_vnt_a, _dp_vnt_b)]
            _dp_ganador = [
                f"🟢 A" if d > 0.5 else (f"🟡 B" if d < -0.5 else "⬛ =")
                for d in _dp_deltas
            ]
            _df_duel = pd.DataFrame({
                "Proyecto":            _dp_labels,
                f"VNT-A ({_duel_a[:12]}) MM": [f"{v:+,.0f}" for v in _dp_vnt_a],
                f"VNT-B ({_duel_b[:12]}) MM": [f"{v:+,.0f}" for v in _dp_vnt_b],
                "Δ A−B (MM)":          [f"{d:+,.0f}" for d in _dp_deltas],
                "Ventaja":             _dp_ganador,
            })

            def _style_delta(val: str) -> str:
                try:
                    v = float(val.replace(",", "").replace("+", ""))
                    if v > 0:
                        return "color:#4ADE80;font-weight:700"
                    elif v < 0:
                        return "color:#F87171;font-weight:700"
                except Exception:
                    pass
                return ""

            _duel_styler = _df_duel.style
            _duel_styler = _style_apply(
                _duel_styler, _style_delta, subset=["Δ A−B (MM)"]
            )
            st.dataframe(_duel_styler, use_container_width=True, hide_index=True)
        else:
            st.caption(
                "ℹ️ Los escenarios guardados no incluyen detalle por proyecto. "
                "Guarda nuevos escenarios desde el Optimizador para habilitar esta vista."
            )

        # ── Frontera de Eficiencia CAPEX→VNT (scatter bubble) ────────────────
        if _da_detail or _db_detail:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("##### Frontera de Eficiencia — CAPEX vs VNT por Proyecto")
            st.caption(
                "Burbuja = proyecto (tamaño ∝ VEI). Cuadrante superior-izquierdo = alta eficiencia. "
                "La línea diagonal marca VEI = 1.0 (VNT = CAPEX)."
            )
            _fe_fig = go.Figure()

            for _fe_detail, _fe_name, _fe_color, _fe_sym in [
                (_da_detail, _duel_a[:22], EPM_GREEN,  "circle"),
                (_db_detail, _duel_b[:22], EPM_AMBER,  "diamond"),
            ]:
                if not _fe_detail:
                    continue
                _fe_x  = [p.get("capex_COP", 0) / 1e6      for p in _fe_detail]
                _fe_y  = [p.get("vnt_COP",   0) / 1e6      for p in _fe_detail]
                _fe_vei= [p.get("vnt_COP", 0) / max(p.get("capex_COP", 1), 1)
                          for p in _fe_detail]
                _fe_lbl= [p.get("name", p.get("id", "?"))[:22] for p in _fe_detail]
                _fe_sz = [max(6, min(40, abs(v) * 8)) for v in _fe_vei]
                _fe_fig.add_trace(go.Scatter(
                    x=_fe_x, y=_fe_y,
                    mode="markers+text",
                    marker=dict(
                        size=_fe_sz, color=_fe_color,
                        symbol=_fe_sym, opacity=0.82,
                        line=dict(width=1, color="rgba(255,255,255,0.4)"),
                    ),
                    text=[f"{l}" for l in _fe_lbl],
                    textposition="top center",
                    textfont=dict(size=8, color="#94A3B8"),
                    name=_fe_name,
                    customdata=list(zip(_fe_vei, _fe_lbl)),
                    hovertemplate=(
                        "<b>%{customdata[1]}</b><br>"
                        "CAPEX: %{x:,.0f} MM COP<br>"
                        "VNT: %{y:+,.0f} MM COP<br>"
                        "VEI: %{customdata[0]:.2f}×<extra></extra>"
                    ),
                ))

            # Línea VEI = 1 (diagonal)
            _fe_all_x = [p.get("capex_COP", 0) / 1e6
                         for p in (_da_detail + _db_detail)]
            _fe_max_x = max(_fe_all_x) * 1.1 if _fe_all_x else 1000
            _fe_fig.add_trace(go.Scatter(
                x=[0, _fe_max_x], y=[0, _fe_max_x],
                mode="lines",
                line=dict(color="rgba(148,163,184,0.25)", width=1.5, dash="dot"),
                name="VEI = 1.0 (umbral)",
                hoverinfo="skip",
            ))
            _fe_fig.add_trace(go.Scatter(
                x=[0, _fe_max_x], y=[0, 2 * _fe_max_x],
                mode="lines",
                line=dict(color="rgba(0,168,107,0.15)", width=1, dash="dash"),
                name="VEI = 2.0",
                hoverinfo="skip",
            ))
            _fe_fig.update_layout(
                height=420,
                margin=dict(t=20, b=60, l=70, r=20),
                xaxis=dict(
                    title="CAPEX (MM COP)", range=[0, _fe_max_x * 1.05],
                    gridcolor="rgba(255,255,255,0.05)", tickfont=dict(size=10)),
                yaxis=dict(
                    title="VNT (MM COP)",
                    gridcolor="rgba(255,255,255,0.05)", tickfont=dict(size=10)),
                legend=dict(orientation="h", y=-0.22, font=dict(size=10)),
                hovermode="closest",
            )
            st.plotly_chart(_fe_fig, use_container_width=True,
                            config={"displayModeBar": "hover"})

            # ── Tabla de eficiencia VEI ───────────────────────────────────────
            with st.expander("📊 Tabla de eficiencia VEI — todos los proyectos"):
                _vei_rows = []
                _seen_ids = set()
                for _pd, _pscen in [(_da_detail, "A"), (_db_detail, "B")]:
                    for _pp in _pd:
                        _pp_id = _pp.get("id", "?")
                        _tag   = "A+B" if _pp_id in _seen_ids else _pscen
                        _seen_ids.add(_pp_id)
                        _pp_vei = (_pp.get("vnt_COP", 0) /
                                   max(_pp.get("capex_COP", 1), 1))
                        _vei_rows.append({
                            "Escenario": _tag,
                            "ID": _pp_id,
                            "Nombre": _pp.get("name", _pp_id)[:30],
                            "CAPEX (MM)": f"{_pp.get('capex_COP',0)/1e6:,.0f}",
                            "VNT (MM)":   f"{_pp.get('vnt_COP',0)/1e6:+,.0f}",
                            "VEI":        round(_pp_vei, 3),
                        })
                _vei_df = pd.DataFrame(sorted(_vei_rows,
                                              key=lambda r: r["VEI"],
                                              reverse=True))
                def _vei_color(v):
                    if isinstance(v, float):
                        if v >= 2.0: return "background:#052e16;color:#4ADE80;font-weight:700"
                        if v >= 1.0: return "background:#0D3321;color:#86EFAC"
                        return "background:#450a0a;color:#FCA5A5"
                    return ""
                st.dataframe(
                    _style_apply(_vei_df.style, _vei_color, subset=["VEI"]),
                    use_container_width=True, hide_index=True,
                )

# ══════════════════════════════════════════════════════════════════════════════
# TAB AUDITORÍA — §5 Earned Value Management (Brecha J — Continuous Planning)
# RFI §5 Gestión del Desempeño · filas 140–147 · PMI PMBOK EVM §7.4
# ══════════════════════════════════════════════════════════════════════════════
with _tab_audi:
    st.divider()
    page_header(
        "Earned Value Management — Seguimiento de Ejecución",
        "PV · AC · EV · SPI · CPI · EAC · Análisis de variaciones · Planificación continua",
        "Brecha J · RFI §5 filas 140–147 · Copperleaf Performance Management",
    )

    # ── Carga de actuals ──────────────────────────────────────────────────────
    _evm_projects = st.session_state.get("projects") or []

    _evm_col_up, _evm_col_yr = st.columns([3, 1], gap="medium")
    with _evm_col_up:
        _evm_file = st.file_uploader(
            "Cargar archivo de gastos reales (Excel o CSV)",
            type=["xlsx", "xls", "csv"],
            key="evm_actuals_upload",
            help=(
                "Columnas mínimas: project_id, ac_cop (costo real COP o MM COP), "
                "pct_complete (avance físico 0–100 o 0.0–1.0). "
                "Columnas opcionales: cut_year, ev_cop, is_paused, benefit_actual_cop."
            ),
        )
    with _evm_col_yr:
        _evm_cut_year = st.number_input(
            "Año de corte",
            min_value=2020, max_value=2040,
            value=datetime.now().year,
            step=1,
            key="evm_cut_year",
            help="Año calendario de referencia para calcular el PV planificado.",
        )

    # Botón de demo (carga actuals sintéticos desde proyectos del portafolio)
    _evm_demo_btn = st.button(
        "🧪 Cargar datos demo (simular ejecución parcial del portafolio)",
        key="evm_demo_btn",
        help="Genera actuals de prueba tomando el 40–70% del CAPEX como AC.",
    )

    _evm_actuals: list = st.session_state.get("evm_actuals", [])

    # ── Parsear archivo subido ────────────────────────────────────────────────
    if _evm_file is not None:
        try:
            _al = ActualsLoader()
            _evm_actuals, _act_report = _al.from_bytes(
                _evm_file.getvalue(), _evm_file.name)
            st.session_state["evm_actuals"] = _evm_actuals
            if _act_report.get("warnings"):
                for _w in _act_report["warnings"]:
                    st.warning(_w)
            st.success(
                f"✅ {_act_report['n_actuals']} proyectos con actuals cargados "
                f"desde «{_evm_file.name}» "
                f"(columnas mapeadas: {', '.join(_act_report.get('mapped_cols',[]))})")
        except Exception as _ex:
            st.error(f"Error al cargar actuals: {_ex}")

    # ── Datos demo ────────────────────────────────────────────────────────────
    if _evm_demo_btn and _evm_projects:
        import random
        _rng = random.Random(42)
        _demo_acts = []
        for _dp in _evm_projects[:8]:   # tomar los primeros 8 proyectos
            _pct = round(_rng.uniform(0.15, 0.85), 2)
            _ac  = _dp.costs.capex_COP * _rng.uniform(0.30, 0.90)
            _demo_acts.append(ProjectActual(
                project_id   = _dp.id,
                ac_COP       = _ac,
                pct_complete = _pct,
                cut_year     = int(_evm_cut_year),
                is_paused    = _rng.random() < 0.10,
            ))
        st.session_state["evm_actuals"] = _demo_acts
        _evm_actuals = _demo_acts
        st.info(f"🧪 {len(_demo_acts)} proyectos demo cargados con avance simulado.")

    # ── Calcular EVM ──────────────────────────────────────────────────────────
    if _evm_actuals and _evm_projects:
        _evm_results, _evm_summary = EVMEngine.compute_portfolio(
            _evm_projects, _evm_actuals, int(_evm_cut_year))

        if not _evm_results:
            st.warning(
                "Ningún project_id de los actuals coincide con el portafolio cargado. "
                "Verifica que los IDs sean idénticos en ambos archivos.")
        else:
            # ── KPIs de portafolio ────────────────────────────────────────────
            _ev_k1, _ev_k2, _ev_k3, _ev_k4 = st.columns(4, gap="medium")

            _spi_p = _evm_summary["spi_portfolio"]
            _cpi_p = _evm_summary["cpi_portfolio"]
            _bv    = _evm_summary["budget_variance_COP"]
            _spi_col = (EPM_RED if _spi_p < 0.75 else
                        EPM_AMBER if _spi_p < 0.90 else EPM_GREEN)
            _cpi_col = (EPM_RED if _cpi_p < 0.80 else
                        EPM_AMBER if _cpi_p < 0.90 else EPM_GREEN)

            with _ev_k1:
                metric_card(
                    "SPI Portafolio",
                    f"{_spi_p:.3f}",
                    ("Cronograma atrasado" if _spi_p < 1 else "En cronograma"),
                    _spi_col)
            with _ev_k2:
                metric_card(
                    "CPI Portafolio",
                    f"{_cpi_p:.3f}",
                    ("Sobre-costo" if _cpi_p < 1 else "Bajo costo"),
                    _cpi_col)
            with _ev_k3:
                _bv_txt = f"{_bv/1e6:+,.0f} MM COP"
                metric_card(
                    "Variación presupuestal (EAC−BAC)",
                    _bv_txt,
                    "Positivo = sobre-costo proyectado",
                    EPM_RED if _bv > 0 else EPM_GREEN)
            with _ev_k4:
                _nr, _na, _nv = (_evm_summary["n_rojo"],
                                  _evm_summary["n_amarillo"],
                                  _evm_summary["n_verde"])
                metric_card(
                    "Semáforo portafolio",
                    f"🔴{_nr}  🟡{_na}  🟢{_nv}",
                    f"{_evm_summary['n_projects']} proyectos en seguimiento",
                    EPM_RED if _nr > 0 else EPM_AMBER if _na > 0 else EPM_GREEN)

            st.markdown("<br>", unsafe_allow_html=True)

            # ── Gráfico S-Curve portafolio: PV · AC · EV ─────────────────────
            _evm_pids  = [r.project_id for r in _evm_results]
            _evm_pvs   = [r.pv_COP  / 1e6 for r in _evm_results]
            _evm_acs   = [r.ac_COP  / 1e6 for r in _evm_results]
            _evm_evs   = [r.ev_COP  / 1e6 for r in _evm_results]
            _evm_eacs  = [r.eac_COP / 1e6 for r in _evm_results]

            _fig_evm = go.Figure()
            _fig_evm.add_trace(go.Bar(
                x=_evm_pids, y=_evm_pvs,
                name="PV — Valor Planificado",
                marker_color="rgba(30,64,175,0.55)",
                hovertemplate="%{x}<br>PV: %{y:,.0f} MM COP<extra></extra>",
            ))
            _fig_evm.add_trace(go.Bar(
                x=_evm_pids, y=_evm_acs,
                name="AC — Costo Real",
                marker_color="rgba(220,38,38,0.65)",
                hovertemplate="%{x}<br>AC: %{y:,.0f} MM COP<extra></extra>",
            ))
            _fig_evm.add_trace(go.Bar(
                x=_evm_pids, y=_evm_evs,
                name="EV — Valor Ganado",
                marker_color="rgba(0,168,107,0.75)",
                hovertemplate="%{x}<br>EV: %{y:,.0f} MM COP<extra></extra>",
            ))
            _fig_evm.add_trace(go.Scatter(
                x=_evm_pids, y=_evm_eacs,
                mode="markers+lines",
                name="EAC — Proyección a completar",
                marker=dict(color=EPM_AMBER, size=8, symbol="diamond"),
                line=dict(color=EPM_AMBER, width=1.5, dash="dot"),
                hovertemplate="%{x}<br>EAC: %{y:,.0f} MM COP<extra></extra>",
            ))
            _fig_evm.update_layout(
                barmode="group",
                height=400,
                margin=dict(t=30, b=80, l=70, r=20),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(15,23,42,0.55)",
                xaxis=dict(
                    title="Proyecto", tickangle=-35,
                    gridcolor="rgba(255,255,255,0.04)", color="#94A3B8"),
                yaxis=dict(
                    title="COP (MM)", gridcolor="rgba(255,255,255,0.05)", color="#94A3B8"),
                legend=dict(
                    orientation="h", y=-0.3,
                    bgcolor="rgba(15,23,42,0.6)", font=dict(size=10, color="#CBD5E1")),
                title=dict(
                    text=(f"EVM Portafolio — Corte {int(_evm_cut_year)}  |  "
                          f"SPI={_spi_p:.3f}  CPI={_cpi_p:.3f}  "
                          f"EAC={_evm_summary['eac_total_COP']/1e6:,.0f} MM COP"),
                    font=dict(size=11, color="#CBD5E1"), x=0, xanchor="left"),
            )
            st.plotly_chart(_fig_evm, use_container_width=True)

            # ── Indicadores SPI / CPI por proyecto (scatter) ─────────────────
            _spi_vals = [r.spi for r in _evm_results]
            _cpi_vals = [r.cpi for r in _evm_results]
            _pct_vals = [r.pct_complete for r in _evm_results]
            _hlth_col = {
                "VERDE":    EPM_GREEN,
                "AMARILLO": EPM_AMBER,
                "ROJO":     EPM_RED,
            }
            _scatter_colors = [_hlth_col.get(r.health, EPM_GRAY) for r in _evm_results]

            _fig_scatter = go.Figure()
            _fig_scatter.add_hline(y=1.0, line_dash="dot", line_color="#94A3B8", line_width=1)
            _fig_scatter.add_vline(x=1.0, line_dash="dot", line_color="#94A3B8", line_width=1)
            _fig_scatter.add_trace(go.Scatter(
                x=_spi_vals, y=_cpi_vals,
                mode="markers+text",
                text=_evm_pids,
                textposition="top center",
                textfont=dict(size=9, color="#94A3B8"),
                marker=dict(
                    color=_scatter_colors,
                    size=[max(10, p * 30) for p in _pct_vals],
                    opacity=0.85,
                    line=dict(color="rgba(255,255,255,0.3)", width=1),
                ),
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "SPI: %{x:.3f}<br>"
                    "CPI: %{y:.3f}<br>"
                    "<extra></extra>"
                ),
            ))
            _fig_scatter.add_annotation(
                x=0.5, y=1.15, text="Bajo cronograma / Bajo costo",
                showarrow=False, font=dict(size=9, color="#94A3B8"))
            _fig_scatter.add_annotation(
                x=1.2, y=1.15, text="En cronograma / Bajo costo",
                showarrow=False, font=dict(size=9, color="#4ADE80"))
            _fig_scatter.add_annotation(
                x=0.5, y=0.65, text="Bajo cronograma / Sobre-costo",
                showarrow=False, font=dict(size=9, color="#F87171"))
            _fig_scatter.update_layout(
                height=360,
                margin=dict(t=30, b=50, l=70, r=30),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(15,23,42,0.55)",
                xaxis=dict(
                    title="SPI (Schedule Performance Index)",
                    range=[max(0, min(_spi_vals) - 0.15), max(_spi_vals) + 0.2],
                    gridcolor="rgba(255,255,255,0.04)", color="#94A3B8"),
                yaxis=dict(
                    title="CPI (Cost Performance Index)",
                    range=[max(0, min(_cpi_vals) - 0.15), max(_cpi_vals) + 0.2],
                    gridcolor="rgba(255,255,255,0.05)", color="#94A3B8"),
                title=dict(
                    text="Dispersión SPI vs CPI  — Tamaño = % avance físico",
                    font=dict(size=11, color="#CBD5E1"), x=0, xanchor="left"),
            )

            _scol_l, _scol_r = st.columns([3, 2], gap="large")
            with _scol_l:
                st.plotly_chart(_fig_scatter, use_container_width=True)
            with _scol_r:
                # Tabla resumen compacta con semáforo
                st.markdown("##### Detalle por proyecto")
                _evm_rows = []
                for _r in sorted(_evm_results, key=lambda x: x.spi):
                    _dot = "🔴" if _r.health == "ROJO" else "🟡" if _r.health == "AMARILLO" else "🟢"
                    _evm_rows.append({
                        "":              _dot,
                        "Proyecto":      _r.project_id[:20],
                        "Avance":        f"{_r.pct_complete:.0%}",
                        "SPI":           f"{_r.spi:.2f}",
                        "CPI":           f"{_r.cpi:.2f}",
                        "CV (MM)":       f"{_r.cv_COP/1e6:+,.0f}",
                        "EAC (MM)":      f"{_r.eac_COP/1e6:,.0f}",
                    })
                st.dataframe(
                    pd.DataFrame(_evm_rows),
                    use_container_width=True,
                    hide_index=True,
                    height=340,
                )

            # ── Tabla detallada expandible ────────────────────────────────────
            with st.expander("📋 Tabla EVM completa — PV / AC / EV / ETC / EAC / SV / CV"):
                _det_rows = []
                for _r in _evm_results:
                    _det_rows.append({
                        "Proyecto":        _r.project_name[:36],
                        "BAC (MM)":        f"{_r.capex_total/1e6:,.0f}",
                        "PV (MM)":         f"{_r.pv_COP/1e6:,.0f}",
                        "AC (MM)":         f"{_r.ac_COP/1e6:,.0f}",
                        "EV (MM)":         f"{_r.ev_COP/1e6:,.0f}",
                        "SV (MM)":         f"{_r.sv_COP/1e6:+,.0f}",
                        "CV (MM)":         f"{_r.cv_COP/1e6:+,.0f}",
                        "SPI":             f"{_r.spi:.3f}",
                        "CPI":             f"{_r.cpi:.3f}",
                        "ETC (MM)":        f"{_r.etc_COP/1e6:,.0f}",
                        "EAC (MM)":        f"{_r.eac_COP/1e6:,.0f}",
                        "% Avance":        f"{_r.pct_complete:.1%}",
                        "% Gastado":       f"{_r.pct_spent:.1%}",
                        "Salud":           _r.health,
                        "Detalle":         _r.health_detail[:80],
                    })
                st.dataframe(pd.DataFrame(_det_rows),
                             use_container_width=True, hide_index=True)

            # ── Interpretación ejecutiva ──────────────────────────────────────
            _bv_sign = "sobre-costo de" if _bv > 0 else "ahorro de"
            st.markdown(
                f'<div class="box-info" style="margin-top:12px">'
                f'💡 <b>Planificación Continua:</b> '
                f'Al corte {int(_evm_cut_year)}, el portafolio muestra '
                f'SPI={_spi_p:.3f} ({"atrasado" if _spi_p<1 else "adelantado"} '
                f'vs. cronograma) y CPI={_cpi_p:.3f} '
                f'({"sobre-costo" if _cpi_p<1 else "bajo costo"}). '
                f'El EAC proyectado implica un {_bv_sign} '
                f'<b>${abs(_bv)/1e6:,.0f} MM COP</b> vs. el presupuesto aprobado. '
                f'{_nr} proyecto{"s" if _nr!=1 else ""} en zona roja requiere'
                f'{"n" if _nr!=1 else ""} acción correctiva inmediata. '
                f'Con re-optimización (Fase 2), el presupuesto liberado por '
                f'proyectos bajo-costo puede jalarse hacia inversiones de mayor '
                f'VNT diferidas.</div>',
                unsafe_allow_html=True)

            # ── Re-optimización con Actuals (Capa 3 — Brecha J R9) ────────────
            st.divider()
            st.markdown(
                "#### ♻️ Re-optimización con Actuals — Planificación Continua",
                unsafe_allow_html=False,
            )
            st.markdown(
                "Ejecuta el MILP con las restricciones R9: proyectos **completados** "
                "se excluyen del plan (CAPEX=0, x=0) y proyectos **en curso** se fijan "
                "en su año de ejecución con CAPEX=ETC. El presupuesto liberado se "
                "reasigna automáticamente a proyectos diferidos de mayor VNT.",
            )

            _reopt_col_l, _reopt_col_r = st.columns([2, 1], gap="medium")
            with _reopt_col_l:
                _reopt_tl = st.slider(
                    "Tiempo límite del solver (s)",
                    min_value=10, max_value=300, value=60, step=10,
                    key="evm_reopt_tl",
                    help="Aumentar si el portafolio es grande (>100 proyectos).",
                )
            with _reopt_col_r:
                _reopt_shadow = st.checkbox(
                    "Calcular shadow prices",
                    value=True,
                    key="evm_reopt_shadow",
                )

            if st.button(
                "⚙️ Re-optimizar con Actuals",
                key="evm_reopt_btn",
                type="primary",
                help="Corre el MILP con restricciones R9 usando los actuals cargados.",
            ):
                with st.spinner("Ejecutando re-optimización con actuals …"):
                    try:
                        import copy, traceback as _tb
                        _reopt_cfg  = copy.deepcopy(get_config())
                        _reopt_opt  = EnterpriseOptimizer(config=_reopt_cfg)

                        # Reconstruir periods desde config (mismo patrón que Planeador)
                        _reopt_saved_periods = st.session_state.get("periods")
                        if _reopt_saved_periods:
                            _reopt_periods = _reopt_saved_periods
                        else:
                            _rc = _reopt_cfg
                            _ry_cfg = _rc.get("years", [2025, 2026, 2027, 2028, 2029])
                            _rb_cfg = _rc.get("budget_COP", {})
                            _rr_cfg = _rc.get("resources", {})
                            _rra_cfg = _rc.get("risk_budget_COP", {})
                            _reopt_periods = [
                                ResourcePeriod(
                                    year=int(_yr),
                                    budget_COP=float(_rb_cfg.get(str(_yr),
                                        _rb_cfg.get(_yr, 2e12))),
                                    person_months=float(
                                        _rr_cfg.get(str(_yr),
                                        _rr_cfg.get(_yr, {})
                                        ).get("person_months", 50_000)
                                        if isinstance(_rr_cfg.get(str(_yr),
                                            _rr_cfg.get(_yr)), dict)
                                        else 50_000
                                    ),
                                    risk_budget_COP=float(_rra_cfg.get(str(_yr),
                                        _rra_cfg.get(_yr, 5e12))),
                                )
                                for _yr in _ry_cfg
                            ]

                        _reopt_result = _reopt_opt.optimize(
                            _evm_projects,
                            _reopt_periods,
                            solver_time_limit_s=int(_reopt_tl),
                            compute_shadow_prices=_reopt_shadow,
                            compute_frontier=False,
                            actuals=_evm_actuals,
                        )

                        st.session_state["opt_result"]       = _reopt_result
                        st.session_state["periods"]          = _reopt_periods
                        st.session_state["opt_scenario_run"] = "actuals_reopt"

                        # Resumen rápido
                        _rc_sel  = len(_reopt_result.selected)
                        _rc_comp = len(_reopt_result.completed)
                        _rc_post = len(_reopt_result.postponed)
                        _rc_vnt  = _reopt_result.total_net_value / 1e6
                        _rc_cap  = _reopt_result.total_capex / 1e6

                        st.success(
                            f"✅ Re-optimización completada · Status: **{_reopt_result.solver_status}** · "
                            f"Tiempo: {_reopt_result.solve_time_s:.1f}s"
                        )
                        _rkc1, _rkc2, _rkc3, _rkc4 = st.columns(4)
                        _rkc1.metric("Proyectos seleccionados", _rc_sel)
                        _rkc2.metric("Completados (excluidos)", _rc_comp)
                        _rkc3.metric("Diferidos", _rc_post)
                        _rkc4.metric("VNT total (MM COP)", f"{_rc_vnt:,.0f}")

                        if _reopt_result.actuals_summary:
                            _as = _reopt_result.actuals_summary
                            st.markdown(
                                f"**Resumen actuals procesados:** "
                                f"{_as.get('n_total', 0)} proyectos con actuals — "
                                f"{_as.get('n_completed', 0)} completados · "
                                f"{_as.get('n_in_progress', 0)} en curso · "
                                f"{_as.get('n_planned', 0)} planificados. "
                                f"CAPEX liberado por completados: "
                                f"**${_as.get('capex_freed_COP', 0)/1e6:,.0f} MM COP**."
                            )

                        st.info(
                            "El resultado ha sido guardado en el estado del portafolio. "
                            "Ve a la pestaña **Planeador** para ver el plan completo "
                            "con la asignación re-optimizada."
                        )

                    except Exception as _reopt_err:
                        st.error(f"Error en re-optimización: {_reopt_err}")
                        with st.expander("Traceback"):
                            import traceback as _tb2
                            st.code(_tb2.format_exc())

    elif not _evm_projects:
        st.info(
            "Carga primero un portafolio de proyectos en la pestaña **Planeador** "
            "para poder hacer seguimiento EVM.")
    else:
        st.info(
            "Sube un archivo de gastos reales (Excel/CSV) o usa los **datos demo** "
            "para ver las métricas EVM del portafolio actual.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB AUDITORÍA — §6 Outage-Aligned Scheduling (Brecha L)
# RFI §3 "Alineamiento con las Interrupciones" · §4 "Respetando asociaciones"
# ══════════════════════════════════════════════════════════════════════════════
with _tab_audi:
    st.divider()
    page_header(
        "Alineamiento con Interrupciones Programadas",
        "Ventanas de mantenimiento · Restricción R10 · Cruce automático por asset_id",
        "Brecha L · RFI §3–§4 · Copperleaf C55 §5.4 — Planned Outage Calendar",
    )

    _oa_outages: list = st.session_state.get("planned_outages", [])
    _oa_projects = st.session_state.get("projects") or []
    _oa_result: Optional[EnterprisePortfolioResult] = st.session_state.get("opt_result")

    if not _oa_outages:
        st.info(
            "No hay ventanas de mantenimiento cargadas. "
            "Ve a **⚙️ Config** → *Calendario de Interrupciones Programadas* "
            "para definir o importar el calendario corporativo."
        )
    else:
        # ── KPIs de resumen ───────────────────────────────────────────────────
        _oa_require = [p for p in _oa_projects
                       if getattr(p, "require_outage_alignment", False)]
        _oa_cal = OutageCalendar(_oa_outages)
        _oa_no_window = [p for p in _oa_require if not _oa_cal.has_any(p.asset_id)]
        _oa_aligned   = [p for p in _oa_require if _oa_cal.has_any(p.asset_id)]

        _oa_k1, _oa_k2, _oa_k3, _oa_k4 = st.columns(4)
        _oa_k1.metric("Outages en calendario", len(_oa_outages))
        _oa_k2.metric("Proyectos que requieren ventana", len(_oa_require))
        _oa_k3.metric("Con ventana disponible", len(_oa_aligned),
                      delta=f"-{len(_oa_no_window)} sin ventana" if _oa_no_window else None,
                      delta_color="inverse")
        _oa_k4.metric("Activos cubiertos",
                      len(set(o.asset_id for o in _oa_outages)))

        # ── Alertas proyectos sin ventana ─────────────────────────────────────
        if _oa_no_window:
            st.warning(
                f"⚠️ **{len(_oa_no_window)} proyecto(s)** tienen "
                f"`require_outage_alignment=True` pero su `asset_id` **no aparece** "
                f"en el calendario de outages. El MILP los tratará sin restricción R10: "
                + ", ".join(f"`{p.name}`" for p in _oa_no_window[:5])
                + ("…" if len(_oa_no_window) > 5 else "")
            )

        # ── Timeline chart: bandas por año + proyectos ────────────────────────
        st.markdown("##### Timeline de Ventanas de Mantenimiento")

        _fig_oa = go.Figure()

        # Agrupar outages por año para las bandas
        _years_with_outages = sorted(set(o.year for o in _oa_outages))
        _all_years_range = list(range(
            min(_years_with_outages) - 1,
            max(_years_with_outages) + 2
        )) if _years_with_outages else [2025, 2030]

        # Colores por tipo de outage
        _oa_type_color = {
            "maintenance": "rgba(59,130,246,0.15)",
            "forced":      "rgba(239,68,68,0.15)",
            "regulatory":  "rgba(168,85,247,0.15)",
        }
        _oa_type_label = {
            "maintenance": "Mantenimiento",
            "forced":      "Salida Forzada",
            "regulatory":  "Regulatorio",
        }

        # Bandas verticales por año con outage
        for _yr in _years_with_outages:
            _yr_outs = [o for o in _oa_outages if o.year == _yr]
            _types_yr = set(o.outage_type for o in _yr_outs)
            _band_color = _oa_type_color.get(
                list(_types_yr)[0] if len(_types_yr) == 1 else "maintenance",
                "rgba(100,116,139,0.12)"
            )
            _fig_oa.add_vrect(
                x0=_yr - 0.4, x1=_yr + 0.4,
                fillcolor=_band_color, layer="below", line_width=0,
                annotation_text=str(_yr),
                annotation_position="top",
                annotation_font=dict(size=9, color="#94A3B8"),
            )

        # BU colors para los proyectos
        _bu_colors = {
            "TyD": "#3B82F6", "AyA": "#22C55E",
            "Gas": "#F59E0B", "Generacion": "#EF4444", "Todos": "#A78BFA",
        }

        # Scatter: proyectos con require_outage_alignment
        for _oa_p in _oa_require:
            _valid = _oa_cal.valid_years(_oa_p.asset_id)
            _col_p = _bu_colors.get(_oa_p.business_unit, "#94A3B8")
            _col_no = "rgba(239,68,68,0.6)"
            _hover_txt = (
                f"<b>{_oa_p.name}</b><br>"
                f"asset_id: {_oa_p.asset_id}<br>"
                f"BU: {_oa_p.business_unit}<br>"
                f"Ventanas disponibles: {_valid if _valid else 'NINGUNA'}<br>"
                f"Año óptimo: {_oa_p.execution_year}"
            )
            for _v_yr in (_valid if _valid else [_oa_p.execution_year]):
                _fig_oa.add_trace(go.Scatter(
                    x=[_v_yr],
                    y=[_oa_p.asset_id],
                    mode="markers",
                    marker=dict(
                        symbol="diamond",
                        size=14,
                        color=_col_p if _valid else _col_no,
                        line=dict(color="#0F172A", width=1),
                    ),
                    text=_hover_txt,
                    hovertemplate="%{text}<extra></extra>",
                    name=_oa_p.business_unit,
                    showlegend=False,
                ))

        # Leyenda de tipos
        for _ot, _oc in _oa_type_color.items():
            _fig_oa.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers",
                marker=dict(size=10, color=_oc.replace("0.15", "0.8")),
                name=_oa_type_label[_ot], showlegend=True,
            ))

        _fig_oa.update_layout(
            height=max(280, len(set(p.asset_id for p in _oa_require)) * 40 + 80),
            plot_bgcolor="#0F172A",
            paper_bgcolor="#0F172A",
            font=dict(color="#CBD5E1"),
            xaxis=dict(
                title="Año",
                tickmode="array",
                tickvals=_all_years_range,
                gridcolor="#1E293B",
            ),
            yaxis=dict(title="Activo", gridcolor="#1E293B", autorange="reversed"),
            legend=dict(
                orientation="h", y=-0.25,
                bgcolor="rgba(15,23,42,0.6)", font=dict(size=10),
            ),
            margin=dict(t=30, b=80, l=160, r=20),
        )
        st.plotly_chart(_fig_oa, use_container_width=True)

        # ── Tabla de alineamiento ─────────────────────────────────────────────
        with st.expander("📋 Tabla de alineamiento Proyecto → Outage"):
            _oa_tbl_rows = []
            for _oa_p in _oa_require:
                _valid = _oa_cal.valid_years(_oa_p.asset_id)
                _oa_tbl_rows.append({
                    "Proyecto":           _oa_p.name[:40],
                    "BU":                 _oa_p.business_unit,
                    "Asset ID":           _oa_p.asset_id,
                    "Año óptimo":         _oa_p.execution_year,
                    "Ventanas disponibles": ", ".join(str(y) for y in _valid) if _valid else "⚠️ Sin ventana",
                    "R10 activa":         "Sí" if _valid else "No",
                })
            if _oa_tbl_rows:
                st.dataframe(pd.DataFrame(_oa_tbl_rows),
                             use_container_width=True, hide_index=True)
            else:
                st.info("No hay proyectos con `require_outage_alignment=True` en el portafolio.")

        # ── Resultado post-optimización ───────────────────────────────────────
        if _oa_result and _oa_result.outage_schedule:
            st.markdown("##### Asignación resuelta por el optimizador")
            _sched_rows = []
            for _s in _oa_result.outage_schedule:
                _sched_rows.append({
                    "Proyecto":       _s.get("project_name", "")[:40],
                    "Asset ID":       _s.get("asset_id", ""),
                    "Año asignado":   _s.get("year_selected", "—"),
                    "Outage ID":      _s.get("outage_id", "—"),
                    "Nombre outage":  _s.get("outage_name", "—")[:40],
                    "Tipo":           _s.get("outage_type", "—"),
                    "Años válidos":   ", ".join(str(y) for y in _s.get("valid_years", [])) or "—",
                    "R10":            "✅" if _s.get("r10_active") else "—",
                    "Alerta":         _s.get("warning", ""),
                })
            st.dataframe(pd.DataFrame(_sched_rows),
                         use_container_width=True, hide_index=True, height=280)
        elif _oa_result:
            st.info("Ejecuta el optimizador con proyectos que tengan "
                    "`require_outage_alignment=True` para ver la asignación resuelta.")

        # ── Calendario completo de outages ────────────────────────────────────
        with st.expander("📅 Calendario corporativo de outages"):
            _oa_cal_rows = [
                {
                    "ID":       o.outage_id,
                    "Activo":   o.asset_id,
                    "Año":      o.year,
                    "Nombre":   o.name,
                    "Días":     o.duration_days,
                    "Tipo":     o.outage_type,
                }
                for o in sorted(_oa_outages, key=lambda x: (x.asset_id, x.year))
            ]
            st.dataframe(pd.DataFrame(_oa_cal_rows),
                         use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB AUDITORÍA — §7 Workflow de Aprobación (Brecha E)
# RFI §8 Flujos de Trabajo · Copperleaf C55 §7 — Investment Lifecycle
# ISO 55001 §8.1 · §9.1 · Option 1 (declarativo) → Option 2 (catálogo)
# ══════════════════════════════════════════════════════════════════════════════
with _tab_audi:
    st.divider()
    page_header(
        "Tablero de Aprobación de Inversiones",
        "Ciclo de vida · Estados · Bandeja de pendientes · Audit trail ISO 55001",
        "Brecha E · RFI §8 · Copperleaf C55 §7 — Investment Lifecycle",
    )

    # Inicializar store de workflows
    if "workflows" not in st.session_state:
        st.session_state["workflows"] = {}
    _wf_store: Dict[str, ProjectWorkflow] = st.session_state["workflows"]

    # Asegurar que todos los proyectos del portafolio tienen workflow
    _wf_projects = st.session_state.get("projects") or []
    _wf_cycle_active = st.session_state.get("current_user", {}).get("cycle", "PAI-2026")
    for _wfp in _wf_projects:
        WorkflowEngine.ensure_workflow(
            _wf_store, _wfp.id, _wfp.name, _wfp.business_unit, _wf_cycle_active)

    _cu = st.session_state.get("current_user", {})
    _cu_role = _cu.get("role", "analista")
    _cu_name = _cu.get("name", "")
    _cu_bu   = _cu.get("bu", "Todos")

    if not _cu_name:
        st.info("👤 Confirma tu identidad en la barra lateral para ver tu bandeja de pendientes.")
    else:
        st.markdown(
            f'<div class="box-info">👤 Sesión activa: <b>{_cu_name}</b> · '
            f'Rol: <b>{_cu_role}</b> · BU: <b>{_cu_bu}</b> · '
            f'Ciclo: <b>{_cu.get("cycle","PAI-2026")}</b></div>',
            unsafe_allow_html=True)

    # ── KPIs de portafolio por estado ─────────────────────────────────────────
    _wf_summary = WorkflowEngine.summary(_wf_store)
    _wf_k_cols = st.columns(len(WF_STATES))
    for _col, (_state, _label) in zip(_wf_k_cols, WF_STATES.items()):
        _cnt = _wf_summary.get(_state, 0)
        _color = WF_STATE_COLOR.get(_state, "#94A3B8")
        _col.markdown(
            f'<div style="background:{_color}22;border-left:3px solid {_color};'
            f'padding:8px;border-radius:6px;text-align:center">'
            f'<div style="font-size:20px;font-weight:700;color:{_color}">{_cnt}</div>'
            f'<div style="font-size:10px;color:#94A3B8">{_label.split(" ",1)[-1]}</div>'
            f'</div>', unsafe_allow_html=True)

    st.markdown("---")

    # ── Diagrama de estados del workflow ──────────────────────────────────────
    with st.expander("📐 Diagrama de estados y transiciones del workflow", expanded=False):
        _wf_schema = WorkflowEngine.get_workflow_schema()
        _schema_c1, _schema_c2 = st.columns([3, 1])

        with _schema_c1:
            # Nodos: estados con su color
            _node_x = {"BORRADOR": 0, "ENVIADO": 1, "EN_REVISION": 2,
                        "APROBADO_NEGOCIO": 3, "APROBADO_PORTAFOLIO": 4,
                        "RECHAZADO": 2.5, "DIFERIDO": 3.5}
            _node_y = {"BORRADOR": 1, "ENVIADO": 1, "EN_REVISION": 1,
                        "APROBADO_NEGOCIO": 1, "APROBADO_PORTAFOLIO": 1,
                        "RECHAZADO": 0, "DIFERIDO": 0}

            _fig_wf = go.Figure()

            def _hex_to_rgba(hex_color: str, alpha: float = 0.2) -> str:
                """Convierte '#RRGGBB' a 'rgba(r,g,b,alpha)' para Plotly."""
                h = hex_color.lstrip("#")
                r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                return f"rgba({r},{g},{b},{alpha})"

            # Aristas (transiciones)
            _role_colors = {
                "analista": "#22C55E", "coordinador_ga": "#3B82F6",
                "comite_ga": "#F59E0B", "director": "#EF4444",
            }
            _drawn_edges = set()
            for _tr in _wf_schema["transitions"]:
                _fr, _to, _role = _tr["from"], _tr["to"], _tr["role"]
                _edge_key = (_fr, _to)
                if _edge_key in _drawn_edges:
                    continue
                _drawn_edges.add(_edge_key)
                if _fr not in _node_x or _to not in _node_x:
                    continue
                _x0, _y0 = _node_x[_fr], _node_y[_fr]
                _x1, _y1 = _node_x[_to], _node_y[_to]
                _col_e = _role_colors.get(_role, "#94A3B8")
                _fig_wf.add_trace(go.Scatter(
                    x=[_x0, _x1, None], y=[_y0, _y1, None],
                    mode="lines",
                    line=dict(color=_col_e, width=1.5, dash="dot"),
                    showlegend=False, hoverinfo="skip",
                ))
                # Flecha (anotación)
                _fig_wf.add_annotation(
                    ax=_x0, ay=_y0, x=_x1, y=_y1,
                    axref="x", ayref="y", xref="x", yref="y",
                    showarrow=True, arrowhead=2, arrowsize=1,
                    arrowcolor=_col_e, arrowwidth=1.5,
                )

            # Nodos (estados)
            for _state, _label in _wf_schema["states"].items():
                if _state not in _node_x:
                    continue
                _is_terminal = _state in _wf_schema["terminal"]
                _n_col = WF_STATE_COLOR.get(_state, "#94A3B8")
                _clean_label = _label.split(" ", 1)[-1] if " " in _label else _label
                _fig_wf.add_trace(go.Scatter(
                    x=[_node_x[_state]], y=[_node_y[_state]],
                    mode="markers+text",
                    marker=dict(size=40, color=_hex_to_rgba(_n_col, 0.2),
                                line=dict(color=_n_col, width=2 if _is_terminal else 1)),
                    text=[f"<b>{_clean_label}</b>"],
                    textposition="middle center",
                    textfont=dict(size=9, color=_n_col),
                    hovertext=_state,
                    hoverinfo="text",
                    showlegend=False,
                ))

            _fig_wf.update_layout(
                height=280, margin=dict(l=10, r=10, t=10, b=10),
                xaxis=dict(showgrid=False, zeroline=False, showticklabels=False,
                           range=[-0.3, 4.7]),
                yaxis=dict(showgrid=False, zeroline=False, showticklabels=False,
                           range=[-0.5, 1.7]),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(_fig_wf, use_container_width=True)

        with _schema_c2:
            st.markdown("**Roles**")
            for _role, _color in _role_colors.items():
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:6px;'
                    f'margin:4px 0"><span style="width:12px;height:12px;'
                    f'background:{_color};border-radius:2px;display:inline-block"></span>'
                    f'<span style="font-size:12px">{_role}</span></div>',
                    unsafe_allow_html=True)
            st.markdown("---")
            st.markdown("**Estados terminales**")
            for _ts in _wf_schema["terminal"]:
                _tc = WF_STATE_COLOR.get(_ts, "#94A3B8")
                st.markdown(
                    f'<div style="color:{_tc};font-size:11px;margin:2px 0">'
                    f'● {_ts}</div>', unsafe_allow_html=True)
            st.markdown("---")
            st.markdown(f"**{len(_wf_schema['transitions'])} transiciones** definidas")

    # ── Bandeja de pendientes ─────────────────────────────────────────────────
    _pending = WorkflowEngine.pending_for_role(_wf_store, _cu_role, _cu_bu)
    st.markdown(f"#### 📬 Bandeja de pendientes ({len(_pending)} proyecto{'s' if len(_pending)!=1 else ''})")

    if not _pending:
        st.success("✅ Sin acciones pendientes para tu rol en este momento.")
    else:
        for _wf in _pending:
            _state_color = WF_STATE_COLOR.get(_wf.current_state, "#94A3B8")
            _state_label = WF_STATES.get(_wf.current_state, _wf.current_state)
            _last = _wf.last_transition()
            _last_info = (f"Última acción: **{_last.label}** por {_last.actor} "
                          f"({_last.timestamp[:10]})" if _last else "Sin historial")

            with st.expander(
                f"{_state_label} · **{_wf.project_name}** · BU: {_wf.bu}",
                expanded=False,
            ):
                st.markdown(
                    f'<div style="border-left:4px solid {_state_color};'
                    f'padding:6px 12px;background:{_state_color}11;border-radius:4px">'
                    f'<b>Estado actual:</b> {_state_label}<br>'
                    f'<b>Proyecto ID:</b> {_wf.project_id}<br>'
                    f'{_last_info}<br>'
                    f'<b>Último comentario:</b> {_wf.last_comment() or "—"}'
                    f'</div>', unsafe_allow_html=True)
                st.markdown("")

                # Acciones disponibles para este rol
                _actions = WorkflowEngine.valid_actions(_cu_role, _wf.current_state)
                if _actions:
                    _act_cols = st.columns([2, 3])
                    with _act_cols[0]:
                        _selected_action_idx = st.selectbox(
                            "Acción",
                            range(len(_actions)),
                            format_func=lambda i: f"{_actions[i]['icon']} {_actions[i]['label']}",
                            key=f"wf_action_{_wf.project_id}",
                        )
                    with _act_cols[1]:
                        _wf_comment = st.text_input(
                            "Comentario (obligatorio para rechazos/diferidos)",
                            key=f"wf_comment_{_wf.project_id}",
                            placeholder="Motivo, observaciones…",
                        )
                    if _cu_name and st.button(
                        f"{_actions[_selected_action_idx]['icon']} Ejecutar acción",
                        key=f"wf_exec_{_wf.project_id}",
                        type="primary",
                    ):
                        try:
                            _wf_action = _actions[_selected_action_idx]
                            _closure_kw = _closure_kwargs_for(_wf.project_id, _wf_action)
                            WorkflowEngine.apply(
                                wf=_wf,
                                action=_wf_action,
                                actor=_cu_name,
                                role=_cu_role,
                                bu=_cu_bu,
                                comment=_wf_comment,
                                **_closure_kw,
                            )
                            st.session_state["workflows"][_wf.project_id] = _wf
                            if _wf_action.get("to") == "COMPLETADO" and not _closure_kw:
                                st.warning(
                                    "Proyecto cerrado, pero no se registró la precisión del "
                                    "estimativo: falta el costo real (EVM) para este proyecto "
                                    "en esta sesión. Carga los actuals antes de cerrar si "
                                    "quieres que este cierre calibre las bandas de EPM.",
                                    icon="📐",
                                )
                            st.success(
                                f"✅ '{_actions[_selected_action_idx]['label']}' aplicado a "
                                f"**{_wf.project_name}** → {WF_STATES.get(_wf.current_state,'')}"
                            )
                            st.rerun()
                        except ValueError as _ve:
                            st.error(str(_ve))
                else:
                    st.info("Sin acciones disponibles para tu rol en este estado.")

    # ── Vista completa del portafolio por estado ──────────────────────────────
    st.markdown("---")
    st.markdown("#### 📊 Estado del portafolio completo")

    _wf_filter_state = st.multiselect(
        "Filtrar por estado",
        options=list(WF_STATES.keys()),
        default=[],
        format_func=lambda s: WF_STATES[s],
        key="wf_filter_state",
    )
    _wf_filter_bu = st.selectbox(
        "Filtrar por negocio",
        ["Todos", "TyD", "AyA", "Gas", "Generacion"],
        key="wf_filter_bu",
    )

    _wf_rows = []
    for _wf in _wf_store.values():
        if _wf_filter_state and _wf.current_state not in _wf_filter_state:
            continue
        if _wf_filter_bu != "Todos" and _wf.bu != _wf_filter_bu:
            continue
        _last = _wf.last_transition()
        _wf_rows.append({
            "Estado":        WF_STATES.get(_wf.current_state, _wf.current_state),
            "Proyecto":      _wf.project_name[:40],
            "BU":            _wf.bu,
            "Ciclo":         _wf.cycle,
            "Último actor":  _last.actor if _last else "—",
            "Último rol":    _last.role  if _last else "—",
            "Última acción": _last.label if _last else "—",
            "Fecha":         _last.timestamp[:10] if _last else "—",
            "Comentario":    _wf.last_comment()[:60] or "—",
            "# Pasos":       len(_wf.history),
        })

    if _wf_rows:
        st.dataframe(pd.DataFrame(_wf_rows), use_container_width=True,
                     hide_index=True, height=320)
    else:
        st.info("No hay proyectos que coincidan con el filtro.")

    # ── Historial de auditoría de un proyecto ─────────────────────────────────
    with st.expander("📋 Audit trail completo de un proyecto"):
        _audit_options = {wf.project_id: wf.project_name
                          for wf in _wf_store.values() if wf.history}
        if _audit_options:
            _audit_sel = st.selectbox(
                "Proyecto",
                options=list(_audit_options.keys()),
                format_func=lambda k: _audit_options[k],
                key="wf_audit_sel",
            )
            _audit_wf = _wf_store.get(_audit_sel)
            if _audit_wf:
                _audit_rows = [
                    {
                        "Paso":      i + 1,
                        "De":        WF_STATES.get(t.from_state, t.from_state),
                        "A":         WF_STATES.get(t.to_state,   t.to_state),
                        "Acción":    t.label,
                        "Actor":     t.actor,
                        "Rol":       t.role,
                        "BU":        t.bu,
                        "Timestamp": t.timestamp,
                        "Comentario":t.comment or "—",
                        "SHA-256":   t.snapshot_sha[:12] + "…" if t.snapshot_sha else "—",
                    }
                    for i, t in enumerate(_audit_wf.history)
                ]
                st.dataframe(pd.DataFrame(_audit_rows), use_container_width=True,
                             hide_index=True)
        else:
            st.info("Ningún proyecto tiene historial de workflow todavía.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB REGISTRO DE ACTIVOS — administración standalone (D-14, Task 16)
# ══════════════════════════════════════════════════════════════════════════════
with _tab_registro:
    render_registro_activos()


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — Parámetros del Modelo  (v12.2: fusionado al final de PLANEADOR)
# ══════════════════════════════════════════════════════════════════════════════
with _tab_config:
    page_header("⚙️ Parámetros del Modelo",
                "WACC · Shadow prices · Umbrales de proporcionalidad · Tasas de degradación",
                "config.json")

    cfg = get_config()

    col_cfg1, col_cfg2 = st.columns(2)

    with col_cfg1:
        section_bar("📐 Parámetros Financieros")
        new_wacc = st.slider(
            "WACC Global — fallback (%)", 5.0, 20.0, cfg.get("wacc", 0.10)*100, 0.5,
            help="Usado solo si la BU no tiene WACC específico definido abajo."
        )
        new_horizon = st.number_input(
            "Horizonte de análisis (años)", 5, 30,
            cfg.get("analysis_horizon_years", 10)
        )
        new_cap_inf = st.slider(
            "Inflación CAPEX (%/año)", 0.0, 15.0,
            cfg.get("optimal_timing",{}).get("capex_inflation_rate",0.04)*100, 0.5
        )
        st.caption(f"Factor NPV actual: "
                   f"{(1-(1+new_wacc/100)**(-new_horizon))/(new_wacc/100):.4f}")

        # ── WACC por Unidad de Negocio ─────────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            "**WACC por Unidad de Negocio**  \n"
            "<span style='font-size:0.78rem;color:#94A3B8'>"
            "Copperleaf C55 §3.2 — cada BU tiene perfil regulatorio distinto. "
            "Prioridad sobre el WACC global. Fuente: CREG (TyD/Gas), CRA (AyA), "
            "mercado spot (Generación).</span>",
            unsafe_allow_html=True
        )
        _wacc_bu_cfg = cfg.get("wacc_by_bu", {})
        _wacc_bus = ["TyD", "Generacion", "AyA", "Gas"]
        _wacc_bu_defaults = {"TyD": 9.5, "Generacion": 11.0, "AyA": 9.0, "Gas": 9.5}
        _new_wacc_by_bu = {}
        for _wbu in _wacc_bus:
            _wbu_default = float(_wacc_bu_cfg.get(_wbu, _wacc_bu_defaults[_wbu] / 100)) * 100
            _new_wacc_by_bu[_wbu] = st.number_input(
                f"WACC {_wbu} (%)",
                min_value=1.0, max_value=30.0,
                value=round(_wbu_default, 2),
                step=0.1,
                format="%.1f",
                key=f"wacc_bu_{_wbu}",
                help=f"WACC efectivo para proyectos de {_wbu}. "
                     f"Afecta NPV de beneficios, EAV, CoD y VEI."
            )
        st.caption(
            "ℹ️ Cambiar WACC afecta todos los cálculos: VNT, EAV, CoD, "
            "Factor NPV, Tiempo Óptimo de Intervención."
        )

        st.divider()
        section_bar("⏱️ Horizonte Timing Óptimo por BU")
        st.markdown(
            "<span style='font-size:0.82rem;color:#94a3b8;'>"
            "Ventana t=0..N para buscar el año óptimo de intervención. "
            "Activos de larga vida (Generacion/AyA) necesitan ventana mayor "
            "para capturar su pico Weibull. Sobreescribe el horizonte global.</span>",
            unsafe_allow_html=True
        )
        _ht_ot_cfg = cfg.get("optimal_timing", {})
        _ht_by_bu_cfg = _ht_ot_cfg.get("horizon_years_by_bu", {})
        _ht_bus = ["TyD", "Generacion", "AyA", "Gas"]
        _ht_defaults = {"TyD": 10, "Generacion": 15, "AyA": 15, "Gas": 12}
        _new_horizon_by_bu = {}
        for _hbu in _ht_bus:
            _hbu_default = int(_ht_by_bu_cfg.get(_hbu, _ht_defaults[_hbu]))
            _new_horizon_by_bu[_hbu] = st.number_input(
                f"Horizonte {_hbu} (años)",
                min_value=3, max_value=20,
                value=_hbu_default,
                step=1,
                format="%d",
                key=f"ht_bu_{_hbu}",
                help=f"Ventana de escaneo timing para {_hbu}. "
                     f"Afecta el año t* óptimo de intervención."
            )
        st.caption(
            "ℹ️ Mayor horizonte = más vigencias evaluadas en curva VNT(t). "
            "Calibrar con vida útil modal (η) del activo predominante en cada BU."
        )

    with col_cfg2:
        section_bar("📏 Umbrales de Proporcionalidad")
        bus_th = ["TyD","AyA","Gas","Generacion","Todos"]
        thresh_cfg = cfg.get("proportionality_thresholds_COP", {})
        for bu_th in bus_th:
            bt = thresh_cfg.get(bu_th, {})
            full_v  = bt.get("full_model_COP", 5_000_000_000)
            simpl_v = bt.get("simplified_model_COP", 500_000_000)
            with st.expander(f"{bu_th}"):
                c1t, c2t = st.columns(2)
                with c1t:
                    st.number_input(f"Full Model (MM COP)", 100, 100_000,
                                     int(full_v/1e6), 500,
                                     key=f"full_{bu_th}")
                with c2t:
                    st.number_input(f"Simplified (MM COP)", 10, 10_000,
                                     int(simpl_v/1e6), 100,
                                     key=f"simp_{bu_th}")

    st.divider()

    section_bar("💰 Precios Regulatorios")
    price_cols = st.columns(3)
    price_fields = [
        ("VD-1-A", "ENS (COP/MWh)", "TyD·Gen"),
        ("VD-1-B", "SAIDI (COP/h-cli)", "TyD"),
        ("VD-1-C", "Agua (COP/m³)", "AyA"),
        ("VD-1-D", "ICTAC (COP/u-h)", "AyA"),
        ("VD-4-A", "CO₂ (COP/tCO₂)", "Todos"),
        ("VD-3-A", "Conexión T&D (COP)", "TyD"),
    ]
    sc_index = {}
    for vd in cfg.get("value_drivers", []):
        for sc in vd.get("sub_criteria", []):
            sc_index[sc["id"]] = sc

    for i, (sc_id, label, bu_note) in enumerate(price_fields):
        sc = sc_index.get(sc_id, {})
        current_price = sc.get("price_per_unit_COP", 0)
        with price_cols[i % 3]:
            st.number_input(
                f"{label} [{bu_note}]",
                0, 100_000_000,
                int(current_price), 1000,
                key=f"price_{sc_id}",
                help=f"Fuente: {sc.get('_fuente','—')}"
            )

    st.divider()

    section_bar("🦠 Tasas de Degradación por Negocio")
    deg_cols = st.columns(4)
    rdeg = cfg.get("optimal_timing",{}).get("risk_growth_rate_by_bu",{})
    for i, bu in enumerate(["TyD","AyA","Gas","Generacion"]):
        with deg_cols[i]:
            st.number_input(
                f"r_deg {bu} (%/año)", 1.0, 50.0,
                float(rdeg.get(bu, 0.15))*100, 0.5,
                key=f"rdeg_{bu}",
            )

    st.divider()

    # ── Ruta de configuración personalizada ──────────────────────────────────
    with st.expander("📂 Ruta de archivo de configuración (avanzado)", expanded=False):
        st.caption(
            "Por defecto el motor carga `config.json` del directorio del módulo. "
            "Especifica una ruta alternativa para trabajar con diferentes entornos "
            "(prod, UAT, pruebas) sin sobrescribir el config principal."
        )
        _cfg_custom_path = st.text_input(
            "Ruta config.json personalizada",
            value=st.session_state.get("custom_config_path", ""),
            placeholder="Ej: C:/EPM/configs/config_uat.json  (vacío = default)",
            key="custom_config_path_input",
        )
        _cp_c1, _cp_c2, _cp_c3 = st.columns(3)
        with _cp_c1:
            if st.button("📥 Cargar desde esta ruta", key="btn_load_custom_cfg"):
                _custom_path = _cfg_custom_path.strip() or None
                try:
                    _loaded_cfg = load_config(path=_custom_path)
                    st.session_state["custom_config_path"] = _custom_path or ""
                    # Invalidar caché para que get_config() recargue
                    get_config.clear()
                    st.success(
                        f"✅ Config cargado desde: "
                        f"`{_custom_path or 'default (config.json)'}` — "
                        f"v{_loaded_cfg.get('_metadata',{}).get('version','?')}"
                    )
                    st.rerun()
                except Exception as _e_cfg:
                    st.error(f"Error al cargar: {_e_cfg}")
        with _cp_c2:
            if st.button("💾 Guardar en esta ruta", key="btn_save_custom_cfg"):
                _custom_path = _cfg_custom_path.strip() or None
                try:
                    cfg["wacc"] = new_wacc / 100
                    cfg["analysis_horizon_years"] = int(new_horizon)
                    _wacc_bu_save2 = {k: v for k, v in cfg.get("wacc_by_bu", {}).items()
                                      if k.startswith("_")}
                    _wacc_bu_save2.update({bu: _new_wacc_by_bu[bu] / 100 for bu in _wacc_bus})
                    _wacc_bu_save2["Todos"] = new_wacc / 100
                    cfg["wacc_by_bu"] = _wacc_bu_save2
                    # Horizonte timing por BU — preservar claves _nota si existen
                    _ht_bu_save2 = {k: v for k, v in cfg.get("optimal_timing", {}).get("horizon_years_by_bu", {}).items()
                                    if k.startswith("_")}
                    _ht_bu_save2.update({bu: _new_horizon_by_bu[bu] for bu in _ht_bus})
                    cfg.setdefault("optimal_timing", {})["horizon_years_by_bu"] = _ht_bu_save2
                    save_config(cfg, path=_custom_path)
                    st.success(f"✅ Guardado en `{_custom_path or 'config.json'}`")
                except Exception as _e_sv:
                    st.error(f"Error al guardar: {_e_sv}")
        with _cp_c3:
            _active_path = st.session_state.get("custom_config_path") or "(default)"
            st.caption(f"Ruta activa: `{_active_path}`")

    # ══════════════════════════════════════════════════════════════════════════
    # PRECISIÓN DE ESTIMATIVOS — desviación real acumulada al cierre
    # ══════════════════════════════════════════════════════════════════════════
    st.divider()
    section_bar("📊 Precisión de Estimativos — Cierre de Proyectos")
    st.caption(
        "Cada proyecto que llega a **COMPLETADO** en el flujo de aprobación registra "
        "aquí su CAPEX inicial contra el costo real (EVM). Es la fuente de la que se "
        "calibran las bandas propias de EPM — mientras un negocio y clase no tengan "
        "muestra suficiente, sus decisiones se siguen apoyando en la referencia de "
        "industria de AACE."
    )

    _acc_store = get_accuracy_store()
    _acc_readiness = _acc_store.readiness_report()

    if not _acc_readiness:
        st.info(
            "Sin cierres registrados todavía. Se registran automáticamente cuando un "
            "proyecto pasa a **COMPLETADO** en la pestaña de Auditoría → Workflow, "
            "siempre que sus actuals de EVM estén cargados en la sesión.",
            icon="📊",
        )
    else:
        _df_ready = pd.DataFrame(_acc_readiness).rename(columns={
            "business_unit": "Negocio", "estimate_class": "Clase",
            "n_closures": "Cierres registrados", "min_required": "Mínimo para calibrar",
            "ready": "¿Lista?",
        })
        _df_ready["¿Lista?"] = _df_ready["¿Lista?"].map(
            lambda b: "✅ Calibrable" if b else "⏳ Insuficiente")
        st.dataframe(_df_ready, use_container_width=True, hide_index=True)

        _n_ready = sum(1 for r in _acc_readiness if r["ready"])
        if _n_ready:
            st.success(
                f"{_n_ready} combinación(es) negocio/clase con muestra suficiente. "
                f"Puedes aplicar sus bandas observadas abajo, en vez de la referencia "
                f"de industria.",
                icon="✅",
            )
            with st.expander("Ver bandas observadas por negocio"):
                for _bu in sorted({r["business_unit"] for r in _acc_readiness}):
                    _bands_obs = _acc_store.bands_by_class(business_unit=_bu)
                    if _bands_obs:
                        st.markdown(f"**{_bu}**")
                        for _cls, _b in sorted(_bands_obs.items()):
                            st.caption(
                                f"{_cls}: {_b['low']*100:+.0f}% / {_b['high']*100:+.0f}% "
                                f"— {_b['description']}"
                            )
                st.caption(
                    "Para aplicarlas, copia estos valores dentro de "
                    "`cost_estimate_uncertainty.classes_by_bu` en el editor de abajo "
                    "y marca la casilla de calibración."
                )

    # ══════════════════════════════════════════════════════════════════════════
    # BANDAS DE INCERTIDUMBRE DE ESTIMACIÓN DEL CAPEX
    # ══════════════════════════════════════════════════════════════════════════
    st.divider()
    section_bar("📐 Bandas de Incertidumbre del Estimativo de CAPEX")
    st.markdown(
        "Desviación esperada del CAPEX según la base sobre la que se construyó el "
        "estimativo. Se aplican **por alternativa**, según la base que cada una declara "
        "en el wizard — no por corrida.  \n"
        "<span style='font-size:0.78rem;color:#94A3B8'>"
        "Tratan una sola incertidumbre: qué tan bien se conoce el costo de esa "
        "alternativa. La incertidumbre de condición del activo ya la trata el Weibull, "
        "y la de presupuesto la trata la frontera eficiente. No se superponen."
        "</span>",
        unsafe_allow_html=True,
    )

    _cu_cfg   = cfg.get("cost_estimate_uncertainty", {})
    _cu_cls   = _cu_cfg.get("classes", {})
    _cu_calib = bool(_cu_cfg.get("_calibrado_con_datos_epm", False))

    if not _cu_calib:
        st.warning(
            f"**Bandas no calibradas con datos de EPM.** Los valores actuales son "
            f"referencia de industria ({_cu_cfg.get('_fuente','AACE RP 18R-97')}). "
            "Se reemplazan por las bandas propias cuando el módulo EVM acumule "
            "desviaciones reales de estimativo inicial contra costo final, por clase, "
            "negocio y tipo de intervención. Ante duda, preferir bandas **amplias**: "
            "declarar resuelta una decisión que está en traslape es mucho más caro que "
            "lo contrario.",
            icon="📐",
        )

    _cu_keys = ["Class1", "Class2", "Class3", "Class4", "Class5"]
    _new_bands: dict = {}
    for _ck in _cu_keys:
        _cb = _cu_cls.get(_ck, {})
        _cc1, _cc2, _cc3 = st.columns([3, 1, 1], gap="small")
        with _cc1:
            st.markdown(
                f"<div style='padding-top:26px;font-size:0.80rem;color:{EPM_SLATE}'>"
                f"<b>{_cb.get('label', _ck)}</b><br>"
                f"<span style='font-size:0.72rem;color:#94A3B8'>"
                f"{_cb.get('description','')}</span></div>",
                unsafe_allow_html=True)
        with _cc2:
            _lo = st.number_input(
                f"Baja (%) · {_ck}", min_value=-60.0, max_value=0.0,
                value=float(_cb.get("low", -0.30)) * 100, step=1.0,
                key=f"cu_low_{_ck}", label_visibility="visible")
        with _cc3:
            _hi = st.number_input(
                f"Alta (%) · {_ck}", min_value=0.0, max_value=100.0,
                value=float(_cb.get("high", 0.50)) * 100, step=1.0,
                key=f"cu_high_{_ck}", label_visibility="visible")
        _new_bands[_ck] = {
            "label":       _cb.get("label", _ck),
            "description": _cb.get("description", ""),
            "low":  _lo / 100.0,
            "high": _hi / 100.0,
        }

    _cu_d1, _cu_d2, _cu_d3 = st.columns([2, 2, 2], gap="medium")
    with _cu_d1:
        _new_default_cls = st.selectbox(
            "Base asumida si no se declara",
            _cu_keys,
            index=_cu_keys.index(_cu_cfg.get("default_class", "Class5"))
                  if _cu_cfg.get("default_class", "Class5") in _cu_keys else 4,
            format_func=lambda k: _new_bands[k]["label"],
            key="cu_default_class",
            help=("Deliberadamente la más débil: quien quiera que su alternativa "
                  "compita con menos incertidumbre debe declarar por qué su cifra "
                  "es sólida. Relajarlo elimina ese incentivo."),
        )
    with _cu_d2:
        _new_overlap_thr = st.slider(
            "Umbral de traslape (%)", 0, 50,
            int(float(_cu_cfg.get("overlap_threshold", 0.0)) * 100), step=5,
            key="cu_overlap_thr",
            help=("Traslape por encima del cual la elección entre alternativas se "
                  "declara no resuelta por los datos. 0% = cualquier traslape la marca. "
                  "El reporte siempre muestra el porcentaje crudo junto al veredicto."),
        )
    with _cu_d3:
        _new_calib = st.checkbox(
            "Calibradas con datos de EPM", value=_cu_calib,
            key="cu_calibrated",
            help=("Marcar solo cuando las bandas provengan del histórico propio de "
                  "desviaciones, no de la referencia de industria. Cambia los avisos "
                  "que ve el analista sobre el origen del supuesto."),
        )

    save_col, reset_col, _ = st.columns([1,1,3])
    with save_col:
        if st.button("💾 Guardar Configuración", type="primary"):
            # Aplicar cambios al config
            _cu_save = dict(_cu_cfg)
            _cu_save["classes"]        = _new_bands
            _cu_save["default_class"]  = _new_default_cls
            _cu_save["overlap_threshold"] = _new_overlap_thr / 100.0
            _cu_save["_calibrado_con_datos_epm"] = bool(_new_calib)
            cfg["cost_estimate_uncertainty"] = _cu_save
            cfg["wacc"] = new_wacc / 100
            cfg["analysis_horizon_years"] = int(new_horizon)
            cfg.setdefault("optimal_timing",{})["capex_inflation_rate"] = new_cap_inf / 100
            # WACC por BU — preservar clave _nota si existe
            _wacc_bu_save = {k: v for k, v in cfg.get("wacc_by_bu", {}).items()
                             if k.startswith("_")}
            _wacc_bu_save.update({bu: _new_wacc_by_bu[bu] / 100 for bu in _wacc_bus})
            _wacc_bu_save["Todos"] = new_wacc / 100   # Todos = fallback = WACC global
            cfg["wacc_by_bu"] = _wacc_bu_save
            # Horizonte timing por BU — preservar clave _nota si existe
            _ht_bu_save = {k: v for k, v in cfg.get("optimal_timing", {}).get("horizon_years_by_bu", {}).items()
                           if k.startswith("_")}
            _ht_bu_save.update({bu: _new_horizon_by_bu[bu] for bu in _ht_bus})
            cfg.setdefault("optimal_timing", {})["horizon_years_by_bu"] = _ht_bu_save
            # Umbrales de proporcionalidad por BU
            _thresh_save = {}
            for _bu_th in bus_th:
                _thresh_save[_bu_th] = {
                    "full_model_COP":       int(st.session_state.get(f"full_{_bu_th}", 5000)) * 1_000_000,
                    "simplified_model_COP": int(st.session_state.get(f"simp_{_bu_th}", 500)) * 1_000_000,
                }
            cfg["proportionality_thresholds_COP"] = _thresh_save
            # Precios regulatorios de value drivers
            for _sc_id, _label, _bu_note in price_fields:
                _new_price = st.session_state.get(f"price_{_sc_id}")
                if _new_price is None:
                    continue
                for _vd in cfg.get("value_drivers", []):
                    for _sc in _vd.get("sub_criteria", []):
                        if _sc.get("id") == _sc_id:
                            _sc["price_per_unit_COP"] = int(_new_price)
            # Tasas de degradación por negocio
            _rdeg_save = {k: v for k, v in cfg.get("optimal_timing", {}).get("risk_growth_rate_by_bu", {}).items()
                          if k.startswith("_")}
            for _rbu in ["TyD", "AyA", "Gas", "Generacion"]:
                _rdeg_save[_rbu] = st.session_state.get(f"rdeg_{_rbu}", rdeg.get(_rbu, 15.0)) / 100.0
            cfg.setdefault("optimal_timing", {})["risk_growth_rate_by_bu"] = _rdeg_save
            save_config(cfg)
            # Invalidar caché
            get_config.clear()
            st.success("✅ Configuración guardada en config.json")
            st.rerun()
    with reset_col:
        if st.button("🔄 Recargar desde archivo"):
            get_config.clear()
            st.rerun()

    # ── Brecha L: Calendario de Outages ──────────────────────────────────────
    st.divider()
    st.markdown("#### 🔧 Calendario de Interrupciones Programadas — Brecha L")
    st.markdown(
        "Define las ventanas de mantenimiento por activo. Los proyectos con "
        "`require_outage_alignment=True` solo podrán ejecutarse en años con ventana disponible. "
        "El cruce es automático via `asset_id`. Fuente recomendada: PMO EPM / SAP PM.",
        unsafe_allow_html=False,
    )

    # Leer outages actuales del config
    _cfg_outages_raw = cfg.get("planned_outages", {}).get("outages", [])
    _outage_rows = [
        {
            "outage_id":    o.get("outage_id", ""),
            "asset_id":     o.get("asset_id", ""),
            "year":         int(o.get("year", 2026)),
            "name":         o.get("name", ""),
            "duration_days":int(o.get("duration_days", 0)),
            "outage_type":  o.get("outage_type", "maintenance"),
        }
        for o in _cfg_outages_raw
    ]

    _ot_col_l, _ot_col_r = st.columns([4, 1], gap="medium")
    with _ot_col_r:
        if st.button("➕ Nueva fila", key="outage_add_row"):
            _outage_rows.append({
                "outage_id": f"OUT-{len(_outage_rows)+1:03d}",
                "asset_id":  "",
                "year":      2026,
                "name":      "",
                "duration_days": 0,
                "outage_type": "maintenance",
            })

    _edited_outages = st.data_editor(
        pd.DataFrame(_outage_rows) if _outage_rows else pd.DataFrame(columns=[
            "outage_id", "asset_id", "year", "name", "duration_days", "outage_type"
        ]),
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key="outage_editor",
        column_config={
            "outage_id":    st.column_config.TextColumn("ID Outage", width="medium"),
            "asset_id":     st.column_config.TextColumn("Asset ID", width="medium",
                help="Debe coincidir con ProjectInput.asset_id (ej: TyD-TR-001)"),
            "year":         st.column_config.NumberColumn("Año", min_value=2020, max_value=2040,
                step=1, format="%d"),
            "name":         st.column_config.TextColumn("Nombre / Descripción", width="large"),
            "duration_days":st.column_config.NumberColumn("Días", min_value=0, max_value=365),
            "outage_type":  st.column_config.SelectboxColumn("Tipo",
                options=["maintenance", "forced", "regulatory"]),
        },
    )

    if st.button("💾 Guardar Calendario de Outages", key="outage_save_btn", type="secondary"):
        try:
            _new_outages = _edited_outages.to_dict("records")
            # Validar
            _errors_ot = [
                f"Fila {i+1}: asset_id vacío"
                for i, r in enumerate(_new_outages) if not r.get("asset_id")
            ]
            if _errors_ot:
                for _e in _errors_ot:
                    st.warning(_e)
            else:
                cfg.setdefault("planned_outages", {})["outages"] = _new_outages
                save_config(cfg)
                get_config.clear()
                # Sincronizar session_state
                st.session_state["planned_outages"] = [
                    PlannedOutage(
                        outage_id=r["outage_id"],
                        asset_id=r["asset_id"],
                        year=int(r["year"]),
                        name=r.get("name", ""),
                        duration_days=int(r.get("duration_days", 0)),
                        outage_type=r.get("outage_type", "maintenance"),
                    )
                    for r in _new_outages if r.get("asset_id")
                ]
                st.success(f"✅ {len(_new_outages)} outages guardados en config.json")
        except Exception as _oe:
            st.error(f"Error al guardar outages: {_oe}")

    # Cargar outages al session_state si aún no están
    if "planned_outages" not in st.session_state and _cfg_outages_raw:
        st.session_state["planned_outages"] = [
            PlannedOutage(
                outage_id=o["outage_id"],
                asset_id=o["asset_id"],
                year=int(o["year"]),
                name=o.get("name", ""),
                duration_days=int(o.get("duration_days", 0)),
                outage_type=o.get("outage_type", "maintenance"),
            )
            for o in _cfg_outages_raw if o.get("asset_id")
        ]

    # Vista raw del config
    with st.expander("🔍 Ver config.json completo"):
        st.json(cfg)

# ══════════════════════════════════════════════════════════════════════════════
# FOOTER — ISO 55001 Status Bar con SHA-256 de sesión
# ══════════════════════════════════════════════════════════════════════════════
opt_st = st.session_state.get("opt_result")
plan_status = (f"{len(opt_st.selected)} proy · "
               f"${opt_st.total_net_value/1e9:.2f}B VNT"
               if opt_st else "Sin plan activo")

# SHA-256 de la sesión: hash de config + eval_results + wizard_results
_footer_payload = json.dumps({
    "config_hash":   _config_hash(),
    "projects_hash": _projects_hash() if st.session_state.get("projects") else "",
    "wr_count":      len(st.session_state.get("wizard_results") or []),
    "opt_status":    (opt_st.solver_status if opt_st else "none"),
    "ts":            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
}, default=str)
_footer_sha256   = hashlib.sha256(_footer_payload.encode()).hexdigest()
_footer_sha_short = _footer_sha256[:16] + "…"

_footer_mode_txt = "EXECUTIVE" if st.session_state.get("executive_mode") else "EXPERT"
_footer_mode_col = "#C4B5FD" if st.session_state.get("executive_mode") else "#6EE7B7"
_footer_bu_filt  = st.session_state.get("sidebar_bu_filter", "Todos")

st.markdown(
    f'''<div class="epm-statusbar">
        <div class="status-item">
            <span class="status-dot"></span>
            <span style="color:#6EE7B7;font-weight:700">EPM</span>
            <span>&nbsp;·&nbsp;Decisión Estratégica</span>
        </div>
        <div class="status-item">
            <span style="color:{_footer_mode_col};font-weight:700">{_footer_mode_txt}</span>
            &nbsp;·&nbsp;{_footer_bu_filt}
        </div>
        <div class="status-item">
            ISO 55001:2024 §4.5 &nbsp;·&nbsp; Copperleaf C55 VDM
        </div>
        <div class="status-item">
            Plan: {plan_status}
        </div>
        <div class="status-item" style="margin-left:auto">
            <span class="sha-badge">
                <span style="color:#4ADE80">■</span>
                SHA-256 &nbsp;{_footer_sha_short}
            </span>
        </div>
        <div class="status-item" style="color:#334155">
            {datetime.now().strftime("%Y-%m-%d %H:%M")}
        </div>
    </div>''',
    unsafe_allow_html=True
)
