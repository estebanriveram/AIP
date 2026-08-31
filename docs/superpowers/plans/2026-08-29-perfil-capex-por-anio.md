# Perfil de CAPEX por Año Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hacer que `evaluate()` y la restricción de presupuesto de ambos optimizadores MILP valoren y consuman correctamente `CostInput.capex_by_year` — un perfil de CAPEX por año calendario que ya existe y ya alimenta un importador real de Excel/PEGA/SAP, pero que hoy la valoración ignora por completo y que ambas restricciones de presupuesto (R1) leen con un anclaje que no se reancla al año candidato que el MILP está probando.

**Architecture:** Un nuevo campo `ProjectInput.declared_execution_year` (autocompletado en `__post_init__`) preserva el año originalmente declarado, independiente de cualquier `execution_year` que un escaneo del MILP reasigne temporalmente. Tres puntos de consumo se corrigen para reanclar `capex_by_year` contra ese campo: la valoración (`_evaluate_inner`), la restricción de presupuesto del optimizador simple (`PortfolioOptimizer`, scipy) y la del optimizador de alcance empresarial (`EnterpriseOptimizer`, PuLP). El camino legado (CAPEX escalar único, sin `capex_by_year`) no cambia de comportamiento en ninguno de los tres puntos.

**Tech Stack:** Python 3, `dataclasses`, `numpy`/`scipy.optimize.milp` (optimizador simple), `pulp` (optimizador de alcance empresarial), `unittest`.

**Spec de referencia:** [docs/superpowers/specs/2026-08-29-capex-multiperiodo-eav-preseleccion-design.md](../specs/2026-08-29-capex-multiperiodo-eav-preseleccion-design.md)

---

## Contexto para quien ejecute este plan

Tres defectos, todos ya en el código antes de este plan, ninguno introducido por él:

1. `evaluate()`/`_evaluate_inner()` nunca lee `capex_by_year` — cualquier proyecto importado con CAPEX repartido en varios años calendario tiene hoy un VNT calculado como si todo el CAPEX se pagara de una sola vez.
2. Ni la valoración ni las restricciones de presupuesto reanclan `capex_by_year` cuando el motor prueba un año de arranque distinto al declarado — el perfil se queda buscando literalmente los años calendario originales.
3. La restricción de presupuesto R1 del optimizador de alcance empresarial mezcla en silencio el camino explícito y el uniforme dentro de la misma ventana de construcción cuando el perfil declarado no cubre todos los años de esa ventana ([epm_core.py:7016-7034](../../../epm_core.py#L7016-L7034)). La del optimizador simple tiene una variante distinta del mismo problema de anclaje ([epm_core.py:5502-5551](../../../epm_core.py#L5502-L5551)), presente incluso en el camino uniforme (sin `capex_by_year`), porque `PortfolioOptimizer` no tiene ninguna prueba hoy.

Cada tarea de este plan es TDD: escribir la prueba que hoy fallaría contra el defecto, confirmar que falla, corregir, confirmar que pasa. Al final de las tareas de código hay una campaña de mutación dedicada — no es opcional, es la que demuestra que las pruebas realmente vigilan el comportamiento y no solo el campo que el motor reporta (lección de la campaña S30, documentada en `REGISTRO_ASUNTOS_ABIERTOS.md`).

Comandos de referencia:
- Prueba puntual: `python -m unittest test_suite_epm_full.<Clase>.<metodo> -v`
- Clase completa: `python -m unittest test_suite_epm_full.<Clase> -v`
- Suite completa: `python test_suite_epm_full.py` (busca `^OK$` en la salida — así lo hacen las campañas de mutación existentes)

---

### Task 1: `declared_execution_year` en `ProjectInput`

**Files:**
- Modify: `epm_core.py:2254` (agregar campo tras `require_outage_alignment`, agregar `__post_init__` a la clase — hoy no tiene ninguno)
- Modify: `test_suite_epm_full.py:77-111` (`_make_project`: agregar parámetros opcionales `capex_by_year=None, declared_execution_year=0`)
- Test: `test_suite_epm_full.py` (nueva clase `S31_PerfilCapexPorAnio`, agregar al final del archivo, antes del bloque `if __name__ == "__main__":`)

- [ ] **Step 1: Escribir las pruebas que fallan**

Agregar al final de `test_suite_epm_full.py`, antes de `if __name__ == "__main__":`:

```python
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
```

- [ ] **Step 2: Confirmar que fallan**

Run: `python -m unittest test_suite_epm_full.S31_PerfilCapexPorAnio -v`
Expected: `TypeError: __init__() got an unexpected keyword argument 'declared_execution_year'` en las tres pruebas — el campo todavía no existe.

- [ ] **Step 3: Agregar el campo y `__post_init__` a `ProjectInput`**

En `epm_core.py`, el campo `require_outage_alignment` (línea 2238) es hoy el último de la clase, y su docstring cierra en la línea 2254. Insertar justo después de esa docstring (antes del comentario de sección `SECCIÓN B2` que sigue en la línea 2257):

```python
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
```

- [ ] **Step 4: Confirmar que pasan**

Run: `python -m unittest test_suite_epm_full.S31_PerfilCapexPorAnio -v`
Expected: `OK` con 3 pruebas.

- [ ] **Step 5: Extender el helper `_make_project` (no rompe firmas existentes — son parámetros nuevos con default)**

En `test_suite_epm_full.py:77-111`, modificar la firma y el cuerpo:

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
) -> ProjectInput:
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
        risk=risk,
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

- [ ] **Step 6: Correr toda la suite para confirmar que extender el helper no rompió nada existente**

Run: `python test_suite_epm_full.py`
Expected: `OK` — las 232 pruebas anteriores más las 3 nuevas de este paso, sin regresiones (el helper solo agregó parámetros con default, ninguna llamada existente los pasa).

- [ ] **Step 7: Commit**

```bash
git add epm_core.py test_suite_epm_full.py
git commit -m "feat: declared_execution_year preserva el año originalmente declarado

Ancla necesaria para reanclar costs.capex_by_year cuando un escaneo del
MILP prueba un año de arranque candidato distinto al declarado. Se
autocompleta en __post_init__ (ProjectInput no tenía ninguno) si no se
declara explícitamente; sobrevive cualquier reasignación posterior de
execution_year."
```

---

### Task 2: Valoración de `capex_by_year` en `_evaluate_inner`

**Files:**
- Modify: `epm_core.py:4155-4180` (bloque de CAPEX en `_evaluate_inner`)
- Modify: `epm_core.py` (bloque `return {...}` de `evaluate()`, alrededor de la línea 4267, agregar `capex_schedule_pv_COP`)
- Test: `test_suite_epm_full.py` (clase `S31_PerfilCapexPorAnio`)

- [ ] **Step 1: Escribir las pruebas que fallan**

Agregar a `S31_PerfilCapexPorAnio`:

```python
    def test_capex_by_year_un_tramo_igual_al_legado(self):
        """Un solo tramo en declared_execution_year debe dar exactamente el
        mismo VNT que el camino legado con ese mismo CAPEX como escalar."""
        p_legado = _make_project(pid="P910", capex=5_000_000_000, exec_year=2026)
        r_legado = self.engine.evaluate(p_legado)

        p_perfil = _make_project(
            pid="P911", capex=5_000_000_000, exec_year=2026,
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
```

- [ ] **Step 2: Confirmar que fallan**

Run: `python -m unittest test_suite_epm_full.S31_PerfilCapexPorAnio -v`
Expected: `test_capex_by_year_un_tramo_igual_al_legado` FALLA (el VNT con perfil ignora `capex_by_year` y usa `capex_COP=0` en su lugar — el perfil no se está leyendo). Las otras tres pruebas nuevas también fallan o dan `KeyError: 'capex_schedule_pv_COP'`.

- [ ] **Step 3: Bifurcar el bloque de CAPEX**

En `epm_core.py`, reemplazar el bloque actual (líneas 4155-4166 aproximadamente, dentro de `_evaluate_inner`):

```python
        # 4. Costos (CAPEX + OPEX incremental como medidas negativas)
        # CAPEX se infla por años de postergación (inflación obra civil)
        capex_inflation_rate = self.config.get("capex_inflation_rate", 0.04)
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
```

por:

```python
        # 4. Costos (CAPEX + OPEX incremental como medidas negativas)
        capex_inflation_rate = self.config.get("capex_inflation_rate", 0.04)

        if project.costs.capex_by_year:
            # Perfil explícito por año calendario, pesos nominales ya
            # inflados por el analista — el motor solo descuenta, no infla.
            #
            # offset_k = año_k − declared_execution_year: la FORMA del
            # perfil, fija desde que se declaró — no cambia entre candidatos.
            #
            # El desplazamiento temporal usa deferral_years, EXACTAMENTE
            # como el CAPEX legado (else, abajo) y como los otros tres
            # componentes de la ecuación (beneficios, riesgo, CoD) — es la
            # única referencia temporal compartida de toda la evaluación.
            # execution_year NO participa en esta fórmula.
            #
            # (Versión corregida tras revisión de calidad: la primera
            # versión usaba delta = execution_year − declared_execution_year
            # en vez de deferral_years. EnterpriseOptimizer reasigna
            # execution_year AL CANDIDATO Y ADEMÁS pasa su propio
            # deferral_years — dos referencias que solo coinciden por
            # casualidad. Un proyecto declarado en 2029 evaluado como
            # candidato en el año base del plan (deferral_years=0) daba
            # capex_pv 31% por encima del nominal con la versión anterior.)
            declared = project.declared_execution_year or project.execution_year
            capex = sum(project.costs.capex_by_year.values())
            capex_pv_por_tramo = {
                year: monto / ((1.0 + self.wacc) ** ((year - declared) + deferral_years))
                for year, monto in project.costs.capex_by_year.items()
            }
            capex_pv = sum(capex_pv_por_tramo.values())
        else:
            # Camino legado — sin cambios de comportamiento.
            capex_base = project.costs.capex_COP
            capex = capex_base * ((1.0 + capex_inflation_rate) ** deferral_years) if deferral_years > 0 else capex_base
            capex_pv = capex
            if deferral_years > 0:
                capex_pv = capex / ((1.0 + self.wacc) ** deferral_years)
            capex_pv_por_tramo = {project.execution_year: capex_pv}
```

- [ ] **Step 4: Agregar `capex_schedule_pv_COP` al diccionario de retorno**

En `epm_core.py`, en el `return {...}` de `_evaluate_inner` (busca la línea con `"capex_pv_COP":`, alrededor de la 4254), agregar inmediatamente después:

```python
            "capex_pv_COP":          capex_pv,           # el que entra a la ecuación
            "capex_schedule_pv_COP": capex_pv_por_tramo,  # cada tramo, para auditoría
```

- [ ] **Step 5: Confirmar que pasan**

Run: `python -m unittest test_suite_epm_full.S31_PerfilCapexPorAnio -v`
Expected: `OK` con 8 pruebas (las 3 de la Task 1 más las 5 de este paso).

- [ ] **Step 6: Correr toda la suite**

Run: `python test_suite_epm_full.py`
Expected: `OK` — sin regresiones. El camino legado (sin `capex_by_year`) no cambió una sola línea de su lógica, solo se movió dentro de un `else`.

- [ ] **Step 7: Commit**

```bash
git add epm_core.py test_suite_epm_full.py
git commit -m "fix: evaluate() ahora valora costs.capex_by_year cuando existe

Hasta ahora la valoración ignoraba por completo el perfil de CAPEX por
año calendario que el importador de portafolios ya carga desde datos
reales (Excel/PEGA/SAP) — cualquier proyecto importado con CAPEX
repartido en varios años tenía su VNT calculado como si todo se pagara
de una sola vez. Cada tramo se descuenta ahora por separado y se reancla
al año candidato durante un escaneo (declared_execution_year). El camino
legado de CAPEX escalar no cambia de comportamiento."
```

---

### Task 3: Restricción de presupuesto — `EnterpriseOptimizer`

**Files:**
- Modify: `epm_core.py:7016-7034` (restricción R1)
- Test: `test_suite_epm_full.py` (clase `S31_PerfilCapexPorAnio`)

**Nota:** esta Task pasó por varias rondas de corrección durante su propia ejecución — el subagente implementador escaló tres veces en vez de forzar que una prueba pasara con evidencia débil, y cada escalamiento reveló una instancia adicional del mismo defecto en otro punto del archivo. Lo que sigue es el **estado final**, no la primera versión. El historial completo queda en los commits `1ebb968`, `76a272d`, `094dd8b` y sus mensajes.

- [x] **Step 1: Escribir las pruebas (versión final, tras tres rondas de corrección)**

```python
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
        # El total consumido en todo el plan no puede exceder el CAPEX
        # declarado (6.000M) — si el año 2027 cobra ADEMÁS 3.000M por la
        # rama uniforme, el total sube a 9.000M.
        total_consumido = sum(
            per["used_COP"] for per in result.budget_utilization.values()
        )
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
```

- [x] **Step 2: Confirmar que fallan**

Contra el código sin corregir (commit `c733ed0`), verificado con `git stash`: `test_enterprise_r1_reancla_perfil_al_candidato` falla (`0.0 != 8_000_000_000`), `test_enterprise_r12_totex_reancla_capex_by_year` falla (el proyecto se selecciona cuando no debía). `test_enterprise_r1_no_mezcla_ramas_dentro_de_la_ventana` es evidencia más débil por sí sola (puede pasar por casualidad según los datos) — la campaña de mutación de la Task 5 es la que la confirma de fondo.

- [x] **Step 3: Corregir R1**

En `epm_core.py` (originalmente líneas 7016-7034, la ubicación exacta se desplaza con cada edición previa — ubicar por el comentario `# ── R1: Presupuesto por vigencia`), reemplazar la búsqueda de año calendario literal:

```python
        # ── R1: Presupuesto por vigencia ──────────────────────────────────────
        for t, period in enumerate(periods):
            # CAPEX que consume proyecto i si inicia en s y estamos en t
            expr = []
            for i in range(n):
                cy = max(1, projects[i].construction_years)
                cap_by_year = getattr(projects[i].costs, 'capex_by_year', {}) or {}
                for s in range(T):
                    t_rel = t - s
                    if 0 <= t_rel < cy:
                        if cap_by_year and years[t] in cap_by_year:
                            coef = cap_by_year.get(years[t], 0)
                        else:
                            coef = projects[i].costs.capex_COP / cy
                        if coef > 0:
                            expr.append(coef * x[i][s])
            if expr:
                prob += (pulp.lpSum(expr) <= period.budget_COP,
                         f"Presupuesto_{period.year}")
```

por la versión que reancla `cap_by_year` al candidato `s` y nunca mezcla con la rama uniforme dentro de la misma ventana:

```python
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
```

- [x] **Step 3b: Reanclar `capex_matrix` (alimenta `budget_utilization`, el reporte de utilización)**

Hallado durante la ejecución: el mismo defecto en la matriz que precomputa VNT/CAPEX por candidato (busca por el comentario `# ── Pre-calcular VNT(i,t) y CAPEX(i,t)`). No afecta ninguna decisión del MILP (R1 ya decide bien de forma independiente), pero el reporte de utilización mostraba 0 para cualquier candidato distinto del año calendario literal declarado. Reemplazar:

```python
            cap_by_year = getattr(proj.costs, 'capex_by_year', {}) or {}
            for t, period in enumerate(periods):
                # CAPEX en esta vigencia si el proyecto inicia en t
                if cap_by_year and period.year in cap_by_year:
                    capex_matrix[i][t] = cap_by_year[period.year]
                else:
                    cy = max(1, proj.construction_years)
                    # Distribuir uniformemente sobre cy años desde t
                    capex_matrix[i][t] = cap_total / cy
```

por (nota: la primera versión de esta corrección usó `cap_by_year.get(declared, 0.0)`, que solo devuelve el tramo del año declarado y subestima un perfil de varios tramos — se corrigió a `sum(...)` tras encontrar esto en la propia prueba `test_enterprise_r1_reancla_perfil_al_candidato`):

```python
            cap_by_year = getattr(proj.costs, 'capex_by_year', {}) or {}
            for t, period in enumerate(periods):
                if cap_by_year:
                    # A diferencia de la rama uniforme (que reporta
                    # cap_total/cy, un año de gasto), aquí se reporta el
                    # TOTAL del perfil, atribuido al candidato — reportar
                    # solo el tramo del año declarado subestimaba proyectos
                    # multi-tramo de forma parcial. Asimetría preexistente
                    # entre las dos ramas, documentada aquí, no armonizada
                    # — fuera de alcance de esta corrección.
                    capex_matrix[i][t] = sum(cap_by_year.values())
                else:
                    cy = max(1, proj.construction_years)
                    capex_matrix[i][t] = cap_total / cy
```

- [x] **Step 3c: Reanclar R12 (restricción TOTEX)**

Hallado durante la ejecución: mismo defecto en una restricción real del MILP (no solo reportería) — busca por el comentario `# ── R12: TOTEX por vigencia`. Pasó por dos correcciones, igual que `capex_matrix` (Step 3b) — primero se reancló al candidato con `get(declared, 0.0)` (solo el tramo del año declarado), y una revisión de calidad posterior encontró que eso subestima un perfil de varios tramos frente al techo TOTEX. Reemplazar directamente (versión original → versión final):

```python
                for i, proj in enumerate(projects):
                    cy = max(1, proj.construction_years)
                    cap_by_year = getattr(proj.costs, 'capex_by_year', {}) or {}
                    if cap_by_year and period.year in cap_by_year:
                        capex_this = cap_by_year[period.year]
                    else:
                        capex_this = proj.costs.capex_COP / cy
```

por:

```python
                for i, proj in enumerate(projects):
                    cy = max(1, proj.construction_years)
                    cap_by_year = getattr(proj.costs, 'capex_by_year', {}) or {}
                    declared = proj.declared_execution_year or proj.execution_year
                    # Mismo patrón que capex_matrix (Step 3b): se usa el
                    # total del perfil, no solo el tramo declarado — R12
                    # solo carga TOTEX en la vigencia de inicio (s=t), así
                    # que atribuye el costo completo del proyecto a ese
                    # único candidato, igual que capex_matrix.
                    if cap_by_year:
                        capex_this = sum(cap_by_year.values())
                    else:
                        capex_this = proj.costs.capex_COP / cy
```

Prueba adicional agregada en la corrección final, `test_enterprise_r12_totex_cuenta_todos_los_tramos`: un proyecto de dos tramos (4.000M + 4.000M = 8.000M) contra un techo TOTEX de 5.000M — debe rechazarse. Con `get(declared, 0.0)` solo contaba el primer tramo (4.000M), colándose bajo el techo; con `sum(...)` cuenta los 8.000M reales y lo rechaza correctamente.

- [x] **Step 4: Confirmar que pasan**

Run: `python -m unittest test_suite_epm_full.S31_PerfilCapexPorAnio -v`
Expected: `OK` con 12 pruebas.

- [x] **Step 5: Correr toda la suite**

Run: `python test_suite_epm_full.py`
Expected: `OK` — 244 pruebas. El camino uniforme no cambió su lógica en ninguno de los tres puntos, solo se reescribió sin alterar el resultado para proyectos sin `capex_by_year`.

- [x] **Step 6: Commits**

Cuatro commits documentan el historial completo de esta Task (no se aplanaron en uno solo, para que el porqué de cada corrección quede trazable):

```
1ebb968  fix: R1 (EnterpriseOptimizer) reancla capex_by_year y no mezcla ramas
76a272d  fix: reanclar capex_by_year en R12 y en el reporte de utilización (get(declared) — primera versión, subestimaba perfiles multi-tramo)
094dd8b  fix: capex_matrix reporta el total del perfil, no solo el tramo declarado
9a16d72  fix: R12 cuenta el total del perfil, no solo el primer tramo (misma corrección que 094dd8b, aplicada a R12)
```

**Hallazgo registrado, no corregido en esta Task:** un proyecto obligatorio de valor neto negativo (que el MILP debe ejecutar igual) tiene incentivo a diferirse hasta el último período visible de cualquier horizonte finito, porque ahí un tramo de construcción de varios años cae fuera de la ventana y no consume presupuesto — el mismo comportamiento intencional de la sección 5, pero explotado por el objetivo de minimización de costo. Se verificó que esto **ya existía antes de este plan**, incluso para CAPEX legado sin perfil explícito — no lo introdujo esta Task. Corregirlo de fondo exige una decisión de diseño (acotar qué candidatos son válidos según si su ventana de construcción cabe completa en el horizonte, o documentar el límite explícitamente) — queda como asunto abierto nuevo para el registro, no como parte de este plan.

---

### Task 4: Restricción de presupuesto — `PortfolioOptimizer` (simple)

**Files:**
- Modify: `epm_core.py:5502-5551` (construcción de `capex_matrix` y Restricción 1)
- Test: `test_suite_epm_full.py` (clase `S31_PerfilCapexPorAnio`)

**Nota:** `PortfolioOptimizer` no tiene ninguna prueba en la suite hoy. Las pruebas de este paso son la primera cobertura que recibe — incluyen tanto el camino de `capex_by_year` como una prueba de regresión del camino uniforme, porque el defecto de anclaje que se corrige aquí también afecta a proyectos sin perfil explícito cuando `construction_years > 1` y el proyecto no arranca en el primer período del plan.

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
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
```

- [ ] **Step 2: Confirmar que fallan**

Run: `python -m unittest test_suite_epm_full.S31_PerfilCapexPorAnio.test_portfolio_simple_uniforme_no_ancla_al_primer_periodo test_suite_epm_full.S31_PerfilCapexPorAnio.test_portfolio_simple_reancla_capex_by_year test_suite_epm_full.S31_PerfilCapexPorAnio.test_portfolio_simple_capex_legado_sensible_al_candidato -v`
Expected: las tres FALLAN.

- [ ] **Step 3: Corregir la construcción de `capex_matrix`**

En `epm_core.py:5502-5530` aproximadamente, reemplazar:

```python
        capex_matrix = np.zeros((n_proj, n_years))
        capex_arr = np.zeros(n_proj)

        for i, proj in enumerate(projects):
            capex_arr[i] = proj.costs.capex_COP
            # Leer distribución de CAPEX por vigencia si está disponible
            capex_by_year = getattr(proj.costs, 'capex_by_year', {})
            for t, bp in enumerate(budget_periods):
                if capex_by_year and bp.year in capex_by_year:
                    # Distribución explícita definida por el analista (datos reales)
                    capex_matrix[i, t] = capex_by_year[bp.year]
                else:
                    # Distribución uniforme sobre construction_years
                    cy = max(1, proj.construction_years)
                    start_t = year_map.get(proj.execution_year, 0)
                    if start_t <= t < start_t + cy:
                        capex_matrix[i, t] = capex_arr[i] / cy
            # Evaluar VNT para cada año de inicio posible
            for t, bp in enumerate(budget_periods):
                original_exec = proj.execution_year
                proj.execution_year = bp.year
                r = self.engine.evaluate(proj)
                net_values[i, t] = r["net_value_COP"]
                proj.execution_year = original_exec
```

por:

```python
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
            # optimizador era casi inerte. Mismo patrón que ya usa
            # EnterpriseOptimizer (deferral = period.year - min(years)),
            # reutilizando el base_year que optimize() ya recibe. También
            # es lo que hace que capex_by_year se reancle aquí: bajo la
            # fórmula corregida de la Task 2, esa rama usa deferral_years,
            # no execution_year — sin este cambio, el perfil tampoco se
            # movería con el candidato en este optimizador.
            for t, bp in enumerate(budget_periods):
                original_exec = proj.execution_year
                proj.execution_year = bp.year
                deferral = max(0, bp.year - base_year)
                r = self.engine.evaluate(proj, deferral_years=deferral)
                net_values[i, t] = r["net_value_COP"]
                proj.execution_year = original_exec
```

La Restricción 1 (líneas 5537-5551 actuales, el bucle que usa `t_rel = t - s`) no cambia — ya asumía la semántica relativa que `capex_matrix` ahora sí tiene.

**Nota:** este cambio también hace que el CAPEX legado (sin `capex_by_year`) se vuelva sensible al año candidato en este optimizador — antes era casi inerte para efectos de VNT, un límite preexistente independiente de todo este plan. `PortfolioOptimizer` no tenía pruebas antes de este plan, así que no hay ninguna regresión que romper; es una corrección, no un cambio de comportamiento que alguien dependiera de él.

- [ ] **Step 4: Confirmar que pasan**

Run: `python -m unittest test_suite_epm_full.S31_PerfilCapexPorAnio -v`
Expected: `OK` con 16 pruebas (3 de la Task 1 + 5 de la Task 2 + 4 de la Task 3 + 4 de este paso).

- [ ] **Step 5: Correr toda la suite**

Run: `python test_suite_epm_full.py`
Expected: `OK` — `PortfolioOptimizer` no tenía pruebas previas que pudieran verse afectadas por el cambio de anclaje del camino uniforme.

- [ ] **Step 6: Commit**

```bash
git add epm_core.py test_suite_epm_full.py
git commit -m "fix: PortfolioOptimizer usa una matriz de CAPEX genuinamente relativa

capex_matrix pasa de anclarse al índice del execution_year declarado (o
a buscar el año calendario literal para capex_by_year) a ser relativa a
cualquier candidato que la Restricción 1 pruebe — que es la semántica
que esa restricción ya asumía al desplazarla por t_rel=t-s. Sin este
cambio, un proyecto con construction_years>1 que no arranca en el primer
período del plan quedaba mal contabilizado. PortfolioOptimizer no tenía
ninguna prueba antes de este cambio; las de este commit son su primera
cobertura."
```

**Ampliación de alcance hallada en la revisión de calidad de esta Task (aprobada por el usuario):** `selected["capex_COP"]` y `selected["vei"]`, en **ambos** optimizadores (no solo `PortfolioOptimizer`), seguían usando el escalar `capex_COP` — que un proyecto de perfil explícito suele dejar en 0 — en vez del total real del perfil. Esto no afecta ninguna decisión del MILP (ya corregida), pero sí el reporte impreso (`print_portfolio_report`): un proyecto de perfil explícito aparecía con CAPEX y VEI en cero, aunque el motor lo hubiera contabilizado bien internamente. Es el mismo patrón que ya se corrigió para `budget_utilization` y R12 en la Task 3 — corregido aquí con el mismo criterio (usar la suma del perfil cuando existe, sin tocar la rama del escalar legado). Commit: `0230fcd`.

**Corrección hallada en la revisión final de todo el trabajo (no de una Task individual):** el Step 3 de esta Task, arriba, justifica su cálculo de `deferral` afirmando *"Mismo patrón que ya usa EnterpriseOptimizer (deferral = period.year - min(years)), reutilizando el base_year que optimize() ya recibe"* — esa afirmación es **incorrecta**. `EnterpriseOptimizer` nunca usa el parámetro `base_year` para calcular `deferral_years`; siempre lo calculó como `period.year - min(years de las vigencias recibidas)`. El parámetro `base_year` en `EnterpriseOptimizer` solo se usa para Continuous Planning (EVM/ETC). La revisión final encontró la divergencia empíricamente: con vigencias cuyo primer año no coincide con `base_year`, los dos optimizadores daban VNT distinto para el mismo proyecto (~18% de diferencia en el caso de prueba construido). Coincidían en todo llamador y toda prueba existente solo porque, hasta ese momento, `base_year` siempre igualaba `min(vigencias)` por convención no forzada. Se corrigió `PortfolioOptimizer` para usar `min(bp.year for bp in budget_periods)` — la misma referencia real que ya usa `EnterpriseOptimizer` — en vez de `base_year`, y se agregó una prueba (`test_ambos_optimizadores_coinciden_con_base_year_desalineado`) que fuerza deliberadamente el desalineamiento y confirma que ambos optimizadores acuerdan. Commit: `dc5943f`.

---

### Task 5: Campaña de mutación

**Files:**
- Create: `<scratchpad_actual>/mutacion_capex_perfil.py` (usar el mismo directorio de scratchpad que las campañas anteriores de esta sesión, p. ej. `mutacion_anio_optimo.py`)

**Nota:** esta sección se reescribió con el estado REAL ejecutado — el borrador original (8 mutaciones, `T2-A/B/C`, `T3-A/B`, `T4-A/B/C`) se escribió antes de que las Tasks 2-4 pasaran por varias rondas de corrección propia y no cubría NC-7/NC-8/NC-10 (ver `REGISTRO_ASUNTOS_ABIERTOS.md`, D-9). Quien ejecutó la campaña correctamente ignoró el borrador y diseñó las 12 mutaciones contra el código real — así es como debe hacerse; este documento solo se pone al día para no confundir a quien lo lea después.

- [x] **Step 1: Escribir el script de mutación**

Seguir exactamente el patrón de `mutacion_anio_optimo.py` (copiar los tres archivos a una carpeta aislada, aplicar una sustitución literal verificada única, correr la suite completa, confirmar `MUERTO`). Las 12 mutaciones realmente usadas, cada una reintroduciendo un defecto específico de NC-4 a NC-10 (D-9 del registro):

```python
MUT = [
 # ── Task 1: declared_execution_year ──────────────────────────────────────
 ("AUT1", "declared_execution_year ya no se autocompleta en __post_init__",
  "    def __post_init__(self):\n"
  "        if self.declared_execution_year == 0:\n"
  "            self.declared_execution_year = self.execution_year",
  "    def __post_init__(self):\n"
  "        pass"),

 # ── Task 2: fórmula de valoración de capex_by_year (NC-4/NC-5) ───────────
 ("FORM1", "El defecto original reintroducido: delta=execution_year-declared en vez de deferral_years",
  "            capex_pv_por_tramo = {\n"
  "                year: monto / ((1.0 + self.wacc) ** ((year - declared) + deferral_years))\n"
  "                for year, monto in project.costs.capex_by_year.items()\n"
  "            }",
  "            capex_pv_por_tramo = {\n"
  "                year: monto / ((1.0 + self.wacc) ** ((year - declared) + (project.execution_year - declared)))\n"
  "                for year, monto in project.costs.capex_by_year.items()\n"
  "            }"),

 ("FORM2", "El ancla 'declared' usa execution_year en vez de declared_execution_year",
  "declared = project.declared_execution_year or project.execution_year",
  "declared = project.execution_year"),

 # ── Task 3: EnterpriseOptimizer (NC-6/NC-7/NC-8) ─────────────────────────
 ("ENT-R1-ANCHOR", "R1 deja de reanclar el perfil al candidato s (vuelve a buscar el año literal)",
  "                        delta = years[s] - declared",
  "                        delta = 0"),

 ("ENT-R1-MIX", "R1 vuelve a mezclar ramas: un año sin tramo declarado dentro de la ventana cae al reparto uniforme",
  "                    else:\n"
  "                        t_rel = t - s\n"
  "                        coef = proj.costs.capex_COP / cy if 0 <= t_rel < cy else 0.0\n"
  "                    if coef > 0:\n"
  "                        expr.append(coef * x[i][s])",
  "                    else:\n"
  "                        t_rel = t - s\n"
  "                        coef = proj.costs.capex_COP / cy if 0 <= t_rel < cy else 0.0\n"
  "                    if coef <= 0 and cap_by_year:\n"
  "                        t_rel = t - s\n"
  "                        if 0 <= t_rel < cy:\n"
  "                            coef = proj.costs.capex_COP / cy\n"
  "                    if coef > 0:\n"
  "                        expr.append(coef * x[i][s])"),

 ("ENT-CM-SUM", "capex_matrix (budget_utilization) vuelve a contar solo el tramo declarado, no el total del perfil",
  "capex_matrix[i][t] = sum(cap_by_year.values())",
  "capex_matrix[i][t] = cap_by_year.get(declared, 0.0)"),

 ("ENT-R12-SUM", "R12 (TOTEX) vuelve a contar solo el tramo declarado, no el total del perfil",
  "capex_this = sum(cap_by_year.values())",
  "capex_this = cap_by_year.get(declared, 0.0)"),

 ("ENT-SEL-CAPEX", "selected[] (Enterprise) vuelve a reportar el escalar capex_COP (0) en vez del total del perfil",
  "cap_total = sum(_cap_by_year_rep.values()) if _cap_by_year_rep else proj.costs.capex_COP",
  "cap_total = proj.costs.capex_COP"),

 # ── Task 4: PortfolioOptimizer (NC-9/NC-10) ──────────────────────────────
 ("PORT-CM-PROFILE", "capex_matrix (Portfolio, rama capex_by_year) vuelve a indexar por año calendario literal, no relativo",
  "                for year, monto in capex_by_year.items():\n"
  "                    k = year - declared\n"
  "                    if 0 <= k < n_years:\n"
  "                        capex_matrix[i, k] += monto",
  "                for year, monto in capex_by_year.items():\n"
  "                    k = year_map.get(year, -1)\n"
  "                    if 0 <= k < n_years:\n"
  "                        capex_matrix[i, k] += monto"),

 ("PORT-CM-UNIFORM", "capex_matrix (Portfolio, rama uniforme) vuelve a anclarse al índice del execution_year declarado, no a 0",
  "                for k in range(min(cy, n_years)):\n"
  "                    capex_matrix[i, k] = capex_arr[i] / cy",
  "                start_t = year_map.get(proj.execution_year, 0)\n"
  "                for t in range(n_years):\n"
  "                    if start_t <= t < start_t + cy:\n"
  "                        capex_matrix[i, t] = capex_arr[i] / cy"),

 ("PORT-DEFERRAL", "PortfolioOptimizer deja de calcular y pasar deferral_years en el escaneo de VNT",
  "                deferral = max(0, bp.year - _plan_base_year)\n"
  "                r = self.engine.evaluate(proj, deferral_years=deferral)",
  "                r = self.engine.evaluate(proj)"),

 ("PORT-SEL-CAPEXARR", "capex_arr[i] (Portfolio) ya no se reasigna al total del perfil → selected reporta capex_COP=0",
  "                capex_arr[i] = sum(capex_by_year.values())",
  "                pass"),
]
```

(`PORT-CM-UNIFORM` y `PORT-DEFERRAL` reflejan el código tras el arreglo de `base_year` vs. `min(vigencias)` hallado en la revisión final — ver la nota al final de la Task 4. Si se ejecuta esta campaña contra un estado del código anterior a ese arreglo, ajustar `PORT-DEFERRAL` para usar `base_year` en vez de `_plan_base_year`, verificando primero que el fragmento sea único con `count() == 1`, igual que las siete campañas anteriores.)

- [x] **Step 2: Correr la campaña**

Run: `python <ruta_scratchpad>/mutacion_capex_perfil.py`

**Resultado real:** primera corrida, **10/12 muertos, 2 sobrevivientes** (`ENT-R1-MIX`, `PORT-CM-UNIFORM`) — el mismo patrón de siempre: la prueba existente medía un campo reportado por un camino de código distinto al que la mutación realmente rompía (`budget_utilization`/`capex_matrix` en vez de la restricción R1 misma; el VNT dejaba que la economía del proyecto eligiera el año, sin aislar el defecto de anclaje). Se agregaron `test_enterprise_r1_no_mezcla_ramas_bajo_presion_de_presupuesto` y `test_portfolio_uniforme_no_ancla_al_indice_del_declarado`, cada una forzando presupuesto cero salvo en el escenario que solo es factible bajo el comportamiento correcto. Segunda corrida (completa, desde cero): **12/12 muertos.**

- [x] **Step 3: Commit**

```
fe0e09a  test: campaña de mutación confirma N/N para el perfil de CAPEX por año
```

(El asunto del subject line — quedó literalmente `N/N` en vez de `12/12`, un placeholder sin llenar — se documenta como hallazgo cosmético de la revisión final; el cuerpo del commit sí tiene el detalle correcto. No se corrigió con `commit --amend` porque hay commits posteriores encima y este entorno no soporta rebase interactivo.)

Además, la revisión de la propia campaña encontró un hueco adicional (no una mutación fallida, un mutante que nunca se diseñó): `r["capex_COP"]` **nominal** (no el valor presente) de la rama `capex_by_year` no tenía ninguna prueba dedicada — todas las pruebas de S31 verificaban `capex_pv_COP`. Se agregó `test_capex_cop_nominal_es_la_suma_del_perfil`, verificada manualmente contra una reversión de esa línea. Commit `d832e61`.

---

### Task 6: Actualizar `REGISTRO_ASUNTOS_ABIERTOS.md`

**Files:**
- Modify: `REGISTRO_ASUNTOS_ABIERTOS.md`

- [ ] **Step 1: Leer el registro actual para ubicar la siguiente entrada libre en la sección D**

Run: abrir `REGISTRO_ASUNTOS_ABIERTOS.md` y confirmar cuál es la última entrada `D-N` documentada (al cierre de la sesión anterior era `D-8`).

- [ ] **Step 2: Agregar la entrada `D-9`**

Documentar, siguiendo el formato exacto de las entradas D-1 a D-8 ya existentes: los tres defectos preexistentes encontrados (valoración de `capex_by_year` ausente; falta de reanclaje en valoración y en R1 de ambos optimizadores; mezcla de ramas en R1 del optimizador de alcance empresarial), que ninguno fue introducido por este plan, también R12 (TOTEX) y el reporte de utilización de EnterpriseOptimizer, hallados en la ejecución de la Task 3, con el mismo patrón; y el hallazgo nuevo, no corregido, del incentivo a diferir al borde del horizonte para proyectos obligatorios de valor negativo), el resultado de la campaña de mutación (9/9, ver ajuste de conteo más abajo), y el número de pruebas de la suite antes/después (232 → 247, si no se agregaron pruebas adicionales durante la ejecución real: 15 nuevas en S31, además de una prueba fortalecida durante la revisión de calidad de la Task 2 que no suma al conteo).

- [ ] **Step 3: Actualizar la tabla "Cobertura de la suite" con la fila S31**

```
| S31 | Perfil de CAPEX por año — valoración por tramo vía deferral_years, reanclaje bajo escaneo, R1/R12/reporte de EnterpriseOptimizer sin mezclar ramas, PortfolioOptimizer sensible al candidato | 15 | 15 |
```

(Ajustar el conteo si el Step 2 de la Task 5 obligó a agregar pruebas adicionales.)

- [ ] **Step 4: Commit**

```bash
git add REGISTRO_ASUNTOS_ABIERTOS.md
git commit -m "docs: registrar D-9 — perfil de CAPEX por año

Cierra la brecha de valoración de capex_by_year y el defecto de anclaje
en R1 de ambos optimizadores MILP. Los tres eran preexistentes al
diseño de este plan."
```

---

## Self-Review (completado por quien escribió este plan)

**Cobertura del spec:** sección 3 (modelo de datos) → Task 1. Sección 4 (valoración) → Task 2. Sección 5 (presupuesto, ambos optimizadores) → Tasks 3-4. Sección 7 (bordes: suma=0, tramo fuera de ventana) — cubiertos parcialmente por las pruebas de reanclaje de las Tasks 3-4; si se requiere una prueba explícita de "suma del perfil = 0 dispara la alerta pedagógica existente", se puede agregar como paso adicional en la Task 2 sin cambiar el diseño. Sección 8 (pruebas) → Tasks 2-5. Sección 9 (decisiones registradas) → reflejadas en los comentarios de código de cada Task.

**Placeholders:** ninguno — cada paso de código trae el fragmento completo a insertar y su ubicación exacta.

**Consistencia de tipos/nombres:** `capex_schedule_pv_COP`, `declared_execution_year`, `capex_by_year` se usan de forma consistente en las Tasks 1-4 y en el spec. `PortfolioOptimizer`/`EnterpriseOptimizer`, `BudgetPeriod`/`ResourcePeriod` no se confunden entre sí en ningún paso.

## Execution Handoff

Plan completo y guardado en `docs/superpowers/plans/2026-08-29-perfil-capex-por-anio.md`. Dos opciones de ejecución:

**1. Subagent-Driven (recomendado)** — despliego un subagente nuevo por tarea, reviso entre tareas, iteración rápida.

**2. Ejecución en línea** — ejecuto las tareas en esta misma sesión con executing-plans, por lotes con puntos de revisión.

¿Cuál prefieres?
