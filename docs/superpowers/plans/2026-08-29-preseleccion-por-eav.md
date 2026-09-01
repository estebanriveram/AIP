# Preselección de Alternativas por Valor Anual Equivalente Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que, dentro de cada `exclusion_group_id`, la alternativa que efectivamente llega a competir por presupuesto en ambos optimizadores MILP sea la de mayor Valor Anual Equivalente — no la de mayor valor neto acumulado crudo, que hoy favorece sistemáticamente a la alternativa de vida más larga por pura acumulación.

**Architecture:** Una función independiente, `_preselect_by_eav(projects, engine)`, se ejecuta antes de construir cualquier matriz de ambos optimizadores. Reduce cada grupo de exclusión a una sola alternativa (la de mayor EAV, o la obligatoria si hay una), reportando las descartadas explícitamente. La restricción de exclusión mutua existente en ambos optimizadores se conserva sin cambios, como salvaguarda.

**Tech Stack:** Python 3, `dataclasses`, `unittest`.

**Depende de:** Nada de [2026-08-29-perfil-capex-por-anio.md](2026-08-29-perfil-capex-por-anio.md) — `evaluate()["eav_COP"]` ya funciona hoy con el camino de CAPEX escalar. Puede ejecutarse antes, después, o en paralelo con ese plan.

**Spec de referencia:** [docs/superpowers/specs/2026-08-29-capex-multiperiodo-eav-preseleccion-design.md](../specs/2026-08-29-capex-multiperiodo-eav-preseleccion-design.md), sección 6.

---

### Task 1: Función `_preselect_by_eav`

**Files:**
- Modify: `epm_core.py` (nueva función a nivel de módulo, inmediatamente antes de `class PortfolioOptimizer:`, línea 5459)
- Test: `test_suite_epm_full.py` (nueva clase `S32_PreseleccionPorEAV`, agregar al final del archivo antes de `S31_PerfilCapexPorAnio` o después — el orden entre S31 y S32 no importa; mantener numeración secuencial)

- [x] **Step 1: Escribir las pruebas que fallan (versión corregida — ver nota)**

**Nota:** el borrador original de `test_preseleccion_elige_mayor_eav_no_mayor_vnt` (capex/delta_annual sin `asset_type` en ninguna alternativa) era matemáticamente irrealizable — sin `asset_type`, `get_horizon_for_asset()` cae al mismo `TyD._default=30` para ambas, y con horizonte idéntico `EAV = VNT / mismo_factor_de_anualidad`: una transformación monótona que nunca puede invertir el orden de VNT. Hallado por el subagente implementador durante el Task 1, confirmado independientemente por dos revisiones (spec y calidad), ambas recalculando el escenario a mano contra el motor real. Versión corregida abajo — declara `asset_type` real y distinto para cada alternativa (activos TyD con vidas útiles muy distintas) y recalibra los beneficios anuales para que la separación sea genuina, no un simple reescalado proporcional (que tampoco bastaría, por la misma razón de monotonicidad).

```python
# ─────────────────────────────────────────────────────────────────────────────
# S32 — PRESELECCIÓN DE ALTERNATIVAS POR VALOR ANUAL EQUIVALENTE
# ─────────────────────────────────────────────────────────────────────────────

class S32_PreseleccionPorEAV(unittest.TestCase):
    """Dentro de un exclusion_group_id, gana la alternativa de mayor EAV,
    no la de mayor VNT crudo — salvo que una sea no-discrecional."""

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
        sc_chica = _make_sc("VD-1-A", delta=22_000.0, price=335_000)
        corta = _make_project(
            pid="ALT_CORTA", capex=8_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_chica])],
            exclusion_group_id="NECESIDAD_X",
        )
        corta.asset_type = "sistema_control_SCADA"  # TyD: 10 años
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
```

- [ ] **Step 2: Confirmar que fallan**

Run: `python -m unittest test_suite_epm_full.S32_PreseleccionPorEAV -v`
Expected: `NameError: name '_preselect_by_eav' is not defined` en las cinco pruebas.

- [ ] **Step 3: Implementar la función**

En `epm_core.py`, inmediatamente antes de `class PortfolioOptimizer:` (línea 5459):

```python
def _preselect_by_eav(projects: list, engine: "ValueEngine"):
    """Para cada exclusion_group_id con más de una alternativa, se queda
    solo la de mayor Valor Anual Equivalente — salvo que una de las
    alternativas sea no-discrecional, en cuyo caso esa gana sin importar
    su EAV. Sin esto, el punto donde efectivamente se decide cuál
    alternativa de una necesidad se ejecuta compara net_value_COP crudo,
    que favorece sistemáticamente a la alternativa de vida más larga por
    pura acumulación — el EAV existe precisamente para evitarlo, pero
    hasta ahora no decidía nada.

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
        mandatorias = [p for p in alternativas if p.is_mandatory]
        if len(mandatorias) > 1:
            raise ValueError(
                f"Grupo de exclusión '{gid}': hay {len(mandatorias)} alternativas "
                f"marcadas como no-discrecionales ({', '.join(p.id for p in mandatorias)}). "
                f"Solo una alternativa del grupo puede ejecutarse — a lo sumo "
                f"una puede ser obligatoria."
            )
        if mandatorias:
            ganador = mandatorias[0]
        else:
            ganador = max(alternativas, key=lambda p: engine.evaluate(p)["eav_COP"])
        ganadores.append(ganador)
        descartados.extend((p, ganador) for p in alternativas if p is not ganador)

    return ganadores, descartados
```

- [ ] **Step 4: Confirmar que pasan**

Run: `python -m unittest test_suite_epm_full.S32_PreseleccionPorEAV -v`
Expected: `OK` con 5 pruebas.

- [ ] **Step 5: Correr toda la suite**

Run: `python test_suite_epm_full.py`
Expected: `OK` — función nueva, sin llamadores todavía, cero impacto en el resto.

- [ ] **Step 6: Commit**

```bash
git add epm_core.py test_suite_epm_full.py
git commit -m "feat: _preselect_by_eav reduce cada grupo de exclusión a una alternativa

Compara por Valor Anual Equivalente en vez de valor neto crudo — el EAV
ya existía (calc_eav) precisamente para que un activo de vida larga no
gane por pura acumulación, pero hasta ahora no decidía nada en el punto
donde efectivamente se elige una alternativa. Lo obligatorio sigue
pesando más que el EAV. Todavía sin conectar a ningún optimizador."
```

---

### Task 2: Conectar la preselección a `PortfolioOptimizer` (simple)

**Files:**
- Modify: `epm_core.py:5481-5498` (inicio de `PortfolioOptimizer.optimize`)
- Test: `test_suite_epm_full.py` (clase `S32_PreseleccionPorEAV`)

**Nota:** el Step 1 original de este Task pedía que la descartada por EAV no apareciera "en ninguna de las dos listas en absoluto" — contradice directamente la sección 6 del spec ("las alternativas descartadas no desaparecen: se reportan explícitamente con la razón"). Contradicción encontrada por el subagente implementador, que correctamente se detuvo en vez de resolverla por su cuenta — cambia el contrato público de `PortfolioResult.postponed` que la Task 3 replica para `EnterpriseOptimizer`. Corregido abajo: la prueba verifica las dos cosas que el spec sí pide — no compite por presupuesto, pero sí queda visible con la razón. También usa el mismo `asset_type`/`delta` calibrados del Task 1 (sin horizonte distinto, EAV no puede invertir el orden de VNT).

- [x] **Step 1: Escribir la prueba que falla (versión corregida)**

```python
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
        sc_chica = _make_sc("VD-1-A", delta=22_000.0, price=335_000)
        corta = _make_project(
            pid="OPT_CORTA", capex=8_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_chica])],
            exclusion_group_id="NECESIDAD_OPT",
        )
        corta.asset_type = "sistema_control_SCADA"  # TyD: 10 años
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
```

- [x] **Step 2: Confirmar que falla**

Run: `python -m unittest test_suite_epm_full.S32_PreseleccionPorEAV.test_portfolio_simple_usa_preseleccion -v`
Expected: FALLA — `OPT_LARGA` aparece en `result.selected` (gana por presupuesto sobrado y VNT crudo mayor).

- [ ] **Step 3: Conectar la preselección**

En `epm_core.py`, dentro de `PortfolioOptimizer.optimize`, inmediatamente después de la validación de `MILP_AVAILABLE` y antes de `n_proj = len(projects)` (línea 5494 actual):

```python
        if base_year is None:
            base_year = datetime.now().year

        # Preselección por EAV: reduce cada grupo de exclusión a una sola
        # alternativa ANTES de construir cualquier matriz — el resto del
        # método nunca ve las alternativas descartadas.
        projects, _preselect_descartados = _preselect_by_eav(projects, self.engine)

        n_proj = len(projects)
```

- [x] **Step 4: Reportar las descartadas junto con las postergadas (corregido — ver nota)**

**Nota:** la primera versión de este Step ponía el bucle de descartadas DENTRO de `if result.success:`, igual que el bucle de "sin cupo". La revisión de calidad lo encontró: como `_preselect_descartados` se conoce ANTES de correr el solver, si este resulta infactible (`result.success == False`) el bloque completo se salta y las descartadas por EAV desaparecen en silencio — contradice la garantía de la sección 6 del spec. Corregido: el bucle de descartadas vive FUERA del `if result.success:`, al mismo nivel que el `return PortfolioResult(...)`.

En el mismo método, donde se construye `postponed` (busca `postponed.append(` dentro del bloque `if result.success:` — ese bloque solo debe seguir teniendo el bucle de "Sin cupo presupuestal"), agregar, DESPUÉS de que termine ese bloque `if result.success:` (mismo nivel de indentación que el `if`, no dentro de él) y antes del `return PortfolioResult(...)`:

```python
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
```

- [x] **Step 4b: Prueba de que las descartadas siguen visibles aunque el solver falle**

Agregar a `S32_PreseleccionPorEAV`:

```python
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
        sc_chica = _make_sc("VD-1-A", delta=22_000.0, price=335_000)
        corta = _make_project(
            pid="INF_CORTA", capex=8_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_chica])],
            exclusion_group_id="NECESIDAD_INF", is_mandatory=True,
        )
        corta.asset_type = "sistema_control_SCADA"
        # Presupuesto 0: INF_CORTA (la ganadora por EAV, ahora obligatoria)
        # no cabe en ningún lado — el solver debe reportar infactible.
        # Nota: solo INF_CORTA es obligatoria — si ambas lo fueran,
        # _preselect_by_eav lanzaría su propio ValueError de "dos
        # obligatorias en el mismo grupo" antes de llegar al solver, que no
        # es el escenario que esta prueba quiere ejercitar.
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
```

- [x] **Step 5: Confirmar que pasa**

Run: `python -m unittest test_suite_epm_full.S32_PreseleccionPorEAV -v`
Expected: `OK` con 7 pruebas.

- [x] **Step 6: Correr toda la suite**

Run: `python test_suite_epm_full.py`
Expected: `OK` — la preselección es un no-op para cualquier proyecto sin `exclusion_group_id`, que es el caso de todas las pruebas existentes.

- [x] **Step 7: Commits**

```
f9cd0f8  feat: PortfolioOptimizer aplica la preselección por EAV
66dbc1d  docs: corregir contradicción entre Task 2 Step 1 y la sección 6 del spec
551e17e  fix: descartadas por EAV visibles en postponed aunque el solver falle
```

---

### Task 3: Conectar la preselección a `EnterpriseOptimizer`

**Files:**
- Modify: `epm_core.py:6900-6941` (inicio de `EnterpriseOptimizer.optimize`, antes de la construcción de `vnt`/`capex_matrix`)
- Test: `test_suite_epm_full.py` (clase `S32_PreseleccionPorEAV`)

- [ ] **Step 1: Escribir la prueba que falla**

```python
    def test_enterprise_usa_preseleccion(self):
        """Mismo caso que el del optimizador simple, para EnterpriseOptimizer."""
        opt = EnterpriseOptimizer(config=self.cfg)
        sc_grande = _make_sc("VD-1-A", delta=100_000.0, price=335_000)
        larga = _make_project(
            pid="ENT_LARGA", capex=50_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_grande])],
            exclusion_group_id="NECESIDAD_ENT",
        )
        sc_chica = _make_sc("VD-1-A", delta=15_000.0, price=335_000)
        corta = _make_project(
            pid="ENT_CORTA", capex=8_000_000_000, exec_year=2026,
            drivers=[_make_driver("VD-1", [sc_chica])],
            exclusion_group_id="NECESIDAD_ENT",
        )
        periods = [ResourcePeriod(year=2026, budget_COP=100_000_000_000)]
        result = opt.optimize([larga, corta], periods, base_year=2026)
        todos_los_ids = (
            {s["project_id"] for s in result.selected} |
            {p["project_id"] for p in result.postponed}
        )
        self.assertNotIn("ENT_LARGA", todos_los_ids)
```

- [ ] **Step 2: Confirmar que falla**

Run: `python -m unittest test_suite_epm_full.S32_PreseleccionPorEAV.test_enterprise_usa_preseleccion -v`
Expected: FALLA.

- [ ] **Step 3: Conectar la preselección**

En `epm_core.py`, dentro de `EnterpriseOptimizer.optimize`, inmediatamente después de `if not PULP_OK: raise RuntimeError(...)` (línea 6900-6901) y antes de la construcción de `n`, `T`, `vnt`, `capex_matrix` (línea 6931):

```python
        if not PULP_OK:
            raise RuntimeError("PuLP no instalado. Ejecuta: pip install pulp")

        # Preselección por EAV: reduce cada grupo de exclusión a una sola
        # alternativa ANTES de construir cualquier matriz.
        projects, _preselect_descartados = _preselect_by_eav(projects, self.engine)
```

(Verificar al implementar si hay líneas entre 6901 y 6931 que dependan del `projects` original sin preseleccionar — de haberlas, mover la llamada a `_preselect_by_eav` al punto más temprano posible después de `PULP_OK`, antes de cualquier otro uso de `projects`.)

- [ ] **Step 4: Reportar las descartadas**

Localizar dónde `EnterprisePortfolioResult` arma su lista de postergados (buscar `postponed.append(` dentro de este método) y agregar, con el mismo patrón de la Task 2 Step 4, adaptado a la forma exacta que use esa lista en este optimizador (revisar el diccionario que usan los demás elementos de `postponed` en este método antes de escribir el nuevo — debe tener las mismas claves).

- [ ] **Step 5: Confirmar que pasa**

Run: `python -m unittest test_suite_epm_full.S32_PreseleccionPorEAV -v`
Expected: `OK` con 7 pruebas.

- [ ] **Step 6: Correr toda la suite**

Run: `python test_suite_epm_full.py`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add epm_core.py test_suite_epm_full.py
git commit -m "feat: EnterpriseOptimizer aplica la preselección por EAV

Mismo mecanismo que PortfolioOptimizer: reduce cada grupo de exclusión
a una sola alternativa por EAV antes de construir cualquier matriz del
MILP."
```

**Estado real de los commits:** `dbf7e89` (implementación) + un commit posterior que agrega la aserción `solver_status == "INFEASIBLE"` a la prueba de infactibilidad, encontrada faltante en la revisión de calidad.

**Hallazgo registrado, no corregido en esta Task:** `_preselect_by_eav` (Task 1) no tiene ninguna noción de Gasto Comprometido / Continuous Planning (`actuals`, `pct_complete`, ETC). Si dos alternativas del mismo `exclusion_group_id` compiten y una ya tiene avance real (completada o en curso), la preselección puede descartarla solo por EAV proyectado, con una razón ("Descartada por EAV frente a X") que no distingue "perdió por economía" de "ya se había ejecutado". `PortfolioOptimizer` nunca expuso este caso (no tiene parámetro de `actuals`); `EnterpriseOptimizer` sí, y esta Task conecta la preselección ahí por primera vez.

No es necesariamente un error de cálculo — en teoría de presupuesto de capital, el costo hundido no debería determinar la decisión futura, así que comparar por EAV proyectado puede ser económicamente correcto. La duda real es si `evaluate()` valora la alternativa en curso por su CAPEX **total** original o por el ETC (costo restante) que ya calcula `EVMEngine` para Continuous Planning — si es el total, la comparación no es de igual a igual frente a una alternativa que arranca de cero. Resolverlo exige entender la interacción completa con la lógica de ETC, fuera del alcance de este plan — queda como asunto abierto para el registro.

---

### Task 4: Campaña de mutación

**Files:**
- Create: `<scratchpad_actual>/mutacion_preseleccion_eav.py`

- [ ] **Step 1: Escribir el script de mutación**

Mismo patrón que `mutacion_anio_optimo.py` y `mutacion_capex_perfil.py`. Mutaciones:

```python
MUT = [
 ("T1-A", "La preselección vuelve a comparar por VNT crudo en vez de EAV",
  '            ganador = max(alternativas, key=lambda p: engine.evaluate(p)["eav_COP"])',
  '            ganador = max(alternativas, key=lambda p: engine.evaluate(p)["net_value_COP"])'),

 ("T1-B", "Lo obligatorio deja de tener precedencia sobre el EAV",
  "        if mandatorias:\n            ganador = mandatorias[0]\n        else:",
  "        if False and mandatorias:\n            ganador = mandatorias[0]\n        else:"),

 ("T2-A", "PortfolioOptimizer deja de llamar la preselección",
  "        projects, _preselect_descartados = _preselect_by_eav(projects, self.engine)\n\n        n_proj = len(projects)",
  "        n_proj = len(projects)"),

 ("T3-A", "EnterpriseOptimizer deja de llamar la preselección",
  "        # Preselección por EAV: reduce cada grupo de exclusión a una sola\n        # alternativa ANTES de construir cualquier matriz.\n        projects, _preselect_descartados = _preselect_by_eav(projects, self.engine)",
  "        pass"),
]
```

(Nota para quien ejecute: las mutaciones T2-A y T3-A dependen del texto exacto que haya quedado tras implementar las Tasks 2 y 3 — confirmar cada patrón contra el archivo real antes de correr la campaña, ajustando si la implementación real difirió del fragmento sugerido en esos pasos.)

- [ ] **Step 2: Correr la campaña**

Run: `python <ruta_scratchpad>/mutacion_preseleccion_eav.py`
Expected: `MUERTOS 4/4`. Si algo sobrevive, reforzar la prueba correspondiente antes de continuar — mismo criterio que en todas las campañas anteriores.

- [ ] **Step 3: Commit**

```bash
git add test_suite_epm_full.py
git commit -m "test: campaña de mutación confirma 4/4 para la preselección por EAV"
```

---

### Task 5: Actualizar `REGISTRO_ASUNTOS_ABIERTOS.md`

**Files:**
- Modify: `REGISTRO_ASUNTOS_ABIERTOS.md`

- [ ] **Step 1: Agregar la siguiente entrada `D-N` disponible**

Documentar el cierre de la brecha descrita en el problema 3 de la sección 1 del spec: el EAV ya se calculaba pero no decidía nada; ahora decide dentro de cada grupo de exclusión en ambos optimizadores, con lo obligatorio precediendo sobre el EAV. Referenciar la campaña de mutación (4/4) y el número de pruebas antes/después.

- [ ] **Step 2: Actualizar la tabla "Cobertura de la suite" con la fila S32**

```
| S32 | Preselección por EAV — gana la alternativa de mayor valor anual equivalente dentro de cada grupo de exclusión, salvo obligatoriedad | 7 | 7 |
```

- [ ] **Step 3: Commit**

```bash
git add REGISTRO_ASUNTOS_ABIERTOS.md
git commit -m "docs: registrar cierre de la brecha de preselección por EAV"
```

---

## Self-Review (completado por quien escribió este plan)

**Cobertura del spec:** sección 6 completa → Tasks 1-3. Sección 7 (obligatoria vs. EAV, dos obligatorias) → cubierto en Task 1. Sección 8 (S32, prueba decisiva) → Task 1 Step 1. Campaña de mutación → Task 4.

**Placeholders:** la Task 3 Step 3 y Step 4, y la Task 4 Step 1 (mutaciones T2-A/T3-A) señalan explícitamente que dependen de verificar el texto exacto contra el archivo real en el momento de ejecutar — no son un "TBD" disfrazado: la Task 2 (ejecutada primero) fija el patrón exacto que la Task 3 debe replicar, y ambas tareas ya traen el código completo a insertar; el llamado a verificar es sobre la ubicación exacta dentro de un método largo, no sobre qué escribir.

**Consistencia de tipos/nombres:** `_preselect_by_eav` devuelve siempre `(ganadores, descartados)` con la misma forma en las tres tareas que la usan.

## Execution Handoff

Plan completo y guardado en `docs/superpowers/plans/2026-08-29-preseleccion-por-eav.md`. Mismas dos opciones de ejecución que el plan de perfil de CAPEX — ¿cuál prefieres, y en qué orden ejecutamos los dos planes?
