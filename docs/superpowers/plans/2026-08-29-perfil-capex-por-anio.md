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

    def test_capex_by_year_se_reancla_bajo_escaneo(self):
        """Si execution_year se reasigna a un candidato distinto del
        declarado (lo que hacen ambos optimizadores MILP al escanear), el
        perfil se mueve junto — cada tramo aterriza 'delta' años antes o
        después, no en su año calendario literal original."""
        p = _make_project(
            pid="P913", capex=0, exec_year=2028,  # declarado en 2028
            capex_by_year={2028: 3_000_000_000, 2029: 2_000_000_000},
        )
        p.execution_year = 2026  # el MILP prueba arrancar 2 años antes
        r = self.engine.evaluate(p)
        wacc = self.engine._get_wacc(p.business_unit)

        # delta = 2026 - 2028 = -2; los tramos reanclados caen en offset
        # (2028-2028)+(-2)=-2 y (2029-2028)+(-2)=-1 años desde "hoy" — es
        # decir, ambos tramos habrían ocurrido ANTES del año que se evalúa
        # como referencia, así que se descuentan hacia adelante (exponente
        # negativo → factor > 1).
        capex_pv_esperado = (
            3_000_000_000 / ((1 + wacc) ** -2) +
            2_000_000_000 / ((1 + wacc) ** -1)
        )
        cost_npv_esperado = -1.0 * (capex_pv_esperado + r["opex_incremental_npv_COP"])

        self.assertAlmostEqual(r["cost_npv_COP"], cost_npv_esperado, delta=1.0)

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
            # perfil, fija desde que se declaró. delta = execution_year −
            # declared_execution_year: cuánto se movió el arranque respecto
            # a lo declarado (0 en la evaluación normal; distinto de 0
            # cuando un escaneo del MILP está probando un año candidato).
            # Cada tramo se descuenta a (offset_k + delta) años desde hoy.
            #
            # Deliberadamente NO se suma deferral_years aquí — mezclarlo
            # con delta duplicaría el desplazamiento en los bucles de
            # escaneo que ya reasignan execution_year Y pasan deferral_years
            # calculado desde una referencia distinta (ver EnterpriseOptimizer).
            # El camino legado (else, abajo) sigue usando deferral_years
            # exactamente como siempre.
            declared = project.declared_execution_year or project.execution_year
            delta = project.execution_year - declared
            capex = sum(project.costs.capex_by_year.values())
            capex_pv_por_tramo = {
                year: monto / ((1.0 + self.wacc) ** ((year - declared) + delta))
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
Expected: `OK` con 7 pruebas (las 3 de la Task 1 más las 4 de este paso).

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

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
    def test_enterprise_r1_reancla_perfil_al_candidato(self):
        """Un proyecto con capex_by_year declarado en 2028/2029 debe poder
        seleccionarse arrancando en 2026 — su gasto real debe caer en
        2026/2027 (reanclado), no permanecer buscando 2028/2029."""
        opt = EnterpriseOptimizer(config=self.cfg)
        p = _make_project(
            pid="P920", capex=0, exec_year=2028, construction_years=2,
            capex_by_year={2028: 4_000_000_000, 2029: 4_000_000_000},
            is_mandatory=True,
        )
        periods = [
            ResourcePeriod(year=y, budget_COP=5_000_000_000)
            for y in (2026, 2027, 2028, 2029, 2030)
        ]
        # Presupuesto solo alcanza en 2026/2027 — si el proyecto solo puede
        # gastar en 2028/2029 (sin reanclar), el MILP no puede colocarlo aquí.
        for per in periods:
            if per.year in (2028, 2029):
                per.budget_COP = 0.0
        result = opt.optimize([p], periods, base_year=2026)
        seleccionado = next((s for s in result.selected if s["project_id"] == "P920"), None)
        self.assertIsNotNone(
            seleccionado,
            "El proyecto debía poder ejecutarse en 2026-2027 con su gasto "
            "reanclado; si esto falla, R1 sigue buscando 2028/2029 literales.",
        )

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
            per["used_COP"] for per in result.budget_util.values()
        ) if hasattr(result, "budget_util") else None
        # El total consumido en todo el plan no puede exceder el CAPEX
        # declarado (6.000M) — si el año 2027 cobra ADEMÁS 3.000M por la
        # rama uniforme, el total sube a 9.000M.
        self.assertIsNotNone(total_consumido)
        self.assertLessEqual(total_consumido, 6_000_000_000 + 1.0)
```

- [ ] **Step 2: Confirmar que fallan**

Run: `python -m unittest test_suite_epm_full.S31_PerfilCapexPorAnio.test_enterprise_r1_reancla_perfil_al_candidato test_suite_epm_full.S31_PerfilCapexPorAnio.test_enterprise_r1_no_mezcla_ramas_dentro_de_la_ventana -v`
Expected: la primera FALLA (`seleccionado` es `None` — el proyecto queda postergado porque R1 solo lo ve gastando en 2028/2029, sin presupuesto ahí). La segunda puede pasar o fallar según los datos exactos del reporte de utilización — si pasa, es evidencia débil (falta de cobertura), no evidencia de que el defecto no existe; ambas quedan confirmadas por la campaña de mutación de la Task 5.

- [ ] **Step 3: Corregir R1**

En `epm_core.py:7016-7034`, reemplazar:

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

por:

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

- [ ] **Step 4: Confirmar que pasan**

Run: `python -m unittest test_suite_epm_full.S31_PerfilCapexPorAnio -v`
Expected: `OK` con 9 pruebas.

- [ ] **Step 5: Correr toda la suite**

Run: `python test_suite_epm_full.py`
Expected: `OK` — sin regresiones. El camino uniforme (`else`) de R1 no cambió su lógica (`t_rel = t-s`, `0 <= t_rel < cy`), solo se reescribió sin alterar el resultado para proyectos sin `capex_by_year`.

- [ ] **Step 6: Commit**

```bash
git add epm_core.py test_suite_epm_full.py
git commit -m "fix: R1 (EnterpriseOptimizer) reancla capex_by_year y no mezcla ramas

Dos defectos preexistentes en la restricción de presupuesto: (1) el
perfil explícito nunca se reanclaba al año candidato que el MILP estaba
probando — solo 'existía' en los años calendario literales declarados;
(2) un año de la ventana de construcción sin tramo declarado caía al
reparto uniforme del CAPEX total, cobrando presupuesto dos veces cuando
el perfil era parcial. Ninguno de los dos fue introducido por el trabajo
de esta rama — ambos predatan el perfil de CAPEX por año."
```

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
```

- [ ] **Step 2: Confirmar que fallan**

Run: `python -m unittest test_suite_epm_full.S31_PerfilCapexPorAnio.test_portfolio_simple_uniforme_no_ancla_al_primer_periodo test_suite_epm_full.S31_PerfilCapexPorAnio.test_portfolio_simple_reancla_capex_by_year -v`
Expected: ambas FALLAN.

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
            # Evaluar VNT para cada año de inicio posible
            for t, bp in enumerate(budget_periods):
                original_exec = proj.execution_year
                proj.execution_year = bp.year
                r = self.engine.evaluate(proj)
                net_values[i, t] = r["net_value_COP"]
                proj.execution_year = original_exec
```

La Restricción 1 (líneas 5537-5551 actuales, el bucle que usa `t_rel = t - s`) no cambia — ya asumía la semántica relativa que `capex_matrix` ahora sí tiene.

- [ ] **Step 4: Confirmar que pasan**

Run: `python -m unittest test_suite_epm_full.S31_PerfilCapexPorAnio -v`
Expected: `OK` con 11 pruebas.

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

---

### Task 5: Campaña de mutación

**Files:**
- Create: `<scratchpad_actual>/mutacion_capex_perfil.py` (usar el mismo directorio de scratchpad que las campañas anteriores de esta sesión, p. ej. `mutacion_anio_optimo.py`)

- [ ] **Step 1: Escribir el script de mutación**

Seguir exactamente el patrón de `mutacion_anio_optimo.py` (copiar los tres archivos a una carpeta aislada, aplicar una sustitución literal verificada única, correr la suite completa, confirmar `MUERTO`). Mutaciones a incluir, cada una reintroduciendo un defecto específico de este plan:

```python
MUT = [
 ("T2-A", "capex_by_year deja de leerse — vuelve al escalar aunque exista perfil",
  "        if project.costs.capex_by_year:",
  "        if False and project.costs.capex_by_year:"),

 ("T2-B", "El offset del tramo pierde declared_execution_year — usa execution_year directo",
  "                year: monto / ((1.0 + self.wacc) ** ((year - declared) + delta))",
  "                year: monto / ((1.0 + self.wacc) ** (year - project.execution_year))"),

 ("T2-C", "delta deja de calcularse — el perfil nunca se reancla bajo escaneo",
  "            delta = project.execution_year - declared",
  "            delta = 0"),

 ("T3-A", "R1 Enterprise deja de reanclar — vuelve a buscar el año calendario literal",
  "                        delta = years[s] - declared",
  "                        delta = 0"),

 ("T3-B", "R1 Enterprise vuelve a mezclar ramas dentro de la ventana",
  "                        coef = sum(\n"
  "                            monto for year_k, monto in cap_by_year.items()\n"
  "                            if year_k + delta == years[t]\n"
  "                        )",
  "                        coef = (sum(\n"
  "                            monto for year_k, monto in cap_by_year.items()\n"
  "                            if year_k + delta == years[t]\n"
  "                        ) or proj.costs.capex_COP / cy)"),

 ("T4-A", "PortfolioOptimizer vuelve a anclar en declared en vez de en 0",
  "                    k = year - declared",
  "                    k = year - declared + declared"),

 ("T4-B", "PortfolioOptimizer: camino uniforme deja de usar ventana relativa",
  "                for k in range(min(cy, n_years)):",
  "                for k in range(min(cy, n_years) + 1):"),
]
```

(Nota para quien ejecute: `T4-B` es un ejemplo de mutación de límite — confirmar antes de correrla que produce exactamente una coincidencia de texto en el archivo copiado; si el conteo de ocurrencias no es 1, ajustar el fragmento para que sea único, siguiendo el mismo criterio que las siete campañas anteriores.)

- [ ] **Step 2: Correr la campaña**

Run: `python <ruta_scratchpad>/mutacion_capex_perfil.py`
Expected: `MUERTOS 7/7`. Si algo `SOBREVIVE`, no continuar a la Task 6 — volver a las pruebas de las Tasks 2-4, identificar por qué esa prueba no distingue el comportamiento correcto del mutado (probablemente está verificando un campo reportado en vez de reconstruir el valor con un oráculo independiente — la misma lección de S30), corregir la prueba, y volver a correr la campaña completa hasta `MUERTOS 7/7`.

- [ ] **Step 3: Commit**

```bash
git add test_suite_epm_full.py
git commit -m "test: campaña de mutación confirma 7/7 para el perfil de CAPEX por año

mutacion_capex_perfil.py queda documentada en el registro de asuntos
abiertos, no en el repositorio (vive en el scratchpad de la sesión, como
las siete campañas anteriores)."
```

(Si el Step 2 obligó a corregir alguna prueba, ese cambio va en este mismo commit.)

---

### Task 6: Actualizar `REGISTRO_ASUNTOS_ABIERTOS.md`

**Files:**
- Modify: `REGISTRO_ASUNTOS_ABIERTOS.md`

- [ ] **Step 1: Leer el registro actual para ubicar la siguiente entrada libre en la sección D**

Run: abrir `REGISTRO_ASUNTOS_ABIERTOS.md` y confirmar cuál es la última entrada `D-N` documentada (al cierre de la sesión anterior era `D-8`).

- [ ] **Step 2: Agregar la entrada `D-9`**

Documentar, siguiendo el formato exacto de las entradas D-1 a D-8 ya existentes: los tres defectos preexistentes encontrados (valoración de `capex_by_year` ausente; falta de reanclaje en valoración y en R1 de ambos optimizadores; mezcla de ramas en R1 del optimizador de alcance empresarial), que ninguno fue introducido por este plan, el resultado de la campaña de mutación (7/7), y el número de pruebas de la suite antes/después (232 → 243, si no se agregaron pruebas adicionales durante la ejecución real).

- [ ] **Step 3: Actualizar la tabla "Cobertura de la suite" con la fila S31**

```
| S31 | Perfil de CAPEX por año — valoración por tramo, reanclaje bajo escaneo, R1 de ambos optimizadores sin mezclar ramas | 11 | 11 |
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
