# Perfil de CAPEX multi-periodo y preselección de alternativas por Valor Anual Equivalente

**Fecha:** 2026-08-29
**Estado:** Diseño aprobado por el usuario, pendiente de plan de implementación
**Origen:** Preguntas de usuarios en etapa de calibración sobre (1) si el horizonte de una iniciativa es configurable y (2) cómo resuelve la herramienta la comparación entre alternativas de una misma iniciativa cuando tienen horizontes distintos.

## 1. Problema

Se identificaron tres brechas relacionadas en `epm_core.py`, ninguna una casuística — las tres afectan el patrón normal de un proyecto de activos físicos:

1. **CAPEX multi-año ya capturado, pero nunca valorado.** `CostInput.capex_by_year: dict` ([epm_core.py:2063](../../../epm_core.py#L2063)) ya existe, con una tubería de datos real detrás: el importador de portafolios lee columnas `capex_2026`...`capex_2030` de un Excel/PEGA/SAP real y las carga ahí ([epm_core.py:10254-10336](../../../epm_core.py#L10254)); ambos optimizadores MILP ya lo usan para el consumo de presupuesto ([epm_core.py:5513-5517](../../../epm_core.py#L5513), [epm_core.py:6946-6950](../../../epm_core.py#L6946)). Pero `evaluate()`/`_evaluate_inner()` — la valoración real, el VNT que se reporta y el que entra como objetivo del MILP — nunca lo lee; sigue descontando el escalar `capex_COP` de una sola vez. Cualquier proyecto importado hoy con CAPEX repartido en varios años calendario ya tiene, con alta probabilidad, un VNT mal calculado. Cero cobertura de pruebas sobre `capex_by_year`.

2. **El perfil no se mueve cuando el motor prueba un año de arranque distinto.** `capex_by_year` ancla por año calendario absoluto: si el MILP escanea "¿y si este proyecto arranca en 2026 en vez de 2028?", el perfil declarado (`{2028: X, 2029: Y}`) no se reancla — sigue buscando 2028/2029 literalmente, así que para cualquier año de arranque candidato distinto al declarado, el modelo ve casi cero gasto. Un proyecto con perfil explícito no puede compararse de verdad contra arrancar en otro año.

3. **Comparación de alternativas de horizontes distintos.** El motor ya calcula un Valor Anual Equivalente (`calc_eav()`, [epm_core.py:3461](../../../epm_core.py#L3461)) precisamente para evitar que un activo de vida larga "gane injustamente por acumulación" frente a uno de vida corta — así lo dice el propio comentario del código. Pero el punto donde efectivamente se decide cuál alternativa de un grupo de exclusión (`exclusion_group_id`) se selecciona usa el valor neto acumulado crudo (`net_value_COP`), no el EAV — tanto en el objetivo del MILP simple (`c_obj = -net_values.flatten()`, [epm_core.py:5533](../../../epm_core.py#L5533)) como en el de alcance empresarial (`vnt_cascade`, [epm_core.py:7013](../../../epm_core.py#L7013)). El EAV se calcula, se reporta, pero no decide nada.

Se verificó contra la documentación de Copperleaf disponible que el horizonte de beneficios es, en su marco, un parámetro financiero gobernado de forma uniforme por la organización (DOC-VDM §1.2, "Financial Parameters"), y que su comparación de alternativas documentada usa valor presente total (Figura 14, DOC-RISK §4.4) sin normalización explícita por vida útil visible en esas fuentes — no hay una receta de Copperleaf que copiar aquí para el punto 3; es una decisión propia del motor.

## 2. Alcance

Dentro de alcance:
- Un nuevo campo `ProjectInput.declared_execution_year`, capturado automáticamente al construir el proyecto, que preserva el año de ejecución *originalmente* declarado, independiente de cualquier año candidato que un escaneo del MILP pruebe después.
- Corrección de `_evaluate_inner()` para que valore `capex_by_year` cuando exista — descontando cada tramo a su propio año, reanclado al año candidato que se esté evaluando.
- Corrección del consumo de presupuesto en ambos optimizadores MILP para que el perfil de `capex_by_year` también se reancle al año candidato, en vez de buscar únicamente los años calendario literales declarados.
- Paso de preselección por Valor Anual Equivalente dentro de cada `exclusion_group_id`, antes de construir las matrices del MILP.
- Pruebas guardianas con la misma disciplina de mutación usada en los grupos S23-S30 del registro de asuntos abiertos.

Fuera de alcance (no se toca en este diseño):
- No se crea ningún campo nuevo de perfil de CAPEX — se extiende `capex_by_year`, que ya existe y ya tiene tubería de importación real.
- El perfil de OPEX sigue siendo una anualidad plana.
- No se resuelve aquí la calibración de horizontes por tipo de activo (`asset_horizons_by_bu`) — decisión de calibración de ASPD, independiente.
- No se modifica el mecanismo de año óptimo de esquina (NC-2, abierto en el registro) — problema distinto, documentado por separado.
- No se toca el parámetro `deferral_years` que ya reciben `evaluate()` y los dos bucles de escaneo del MILP para el camino legado (CAPEX escalar) — su comportamiento actual queda intacto para no arriesgar las 232 pruebas existentes que dependen de él.

## 3. Modelo de datos

**No se crea ningún campo nuevo de perfil.** Se reutiliza `CostInput.capex_by_year: dict[int, float]` ([epm_core.py:2063](../../../epm_core.py#L2063)), ya existente:

- Clave: **año calendario absoluto** (no offset) — mismo formato que ya produce el importador (`capex_2026`, `capex_2027`...).
- Valor: monto **nominal**, ya en los pesos que el analista espera pagar efectivamente ese año — el motor no aplica `capex_inflation_rate` sobre estos montos, solo los descuenta. Mismo criterio que ya aplica el importador al sumar las columnas por año para obtener `capex_total`.
- `{}` o no declarado: comportamiento actual sin cambios — el motor usa `capex_COP` como monto único, con su tratamiento de inflación por diferimiento existente.

**Campo nuevo, en `ProjectInput`** (no en `CostInput`) ([epm_core.py:2254](../../../epm_core.py#L2254), agregar tras `require_outage_alignment`):

```python
declared_execution_year: int = 0
"""Año de ejecución tal como fue declarado originalmente — antes de que
cualquier escaneo del MILP o del año óptimo pruebe un año candidato distinto
sobrescribiendo execution_year. Se captura una sola vez en __post_init__ si
no se declara explícitamente. Es el ancla contra la que se mide el offset de
cada tramo de costs.capex_by_year: sin este campo, un perfil de CAPEX
explícito no puede reanclarse cuando el motor prueba un año de arranque
distinto — se queda buscando literalmente los años calendario originales
y el escaneo lo ve como si casi no costara nada en cualquier otro año."""
```

Con `__post_init__` nuevo (la clase no tiene ninguno hoy):

```python
def __post_init__(self):
    if self.declared_execution_year == 0:
        self.declared_execution_year = self.execution_year
```

Esto es seguro para todos los llamadores existentes: tanto el importador ([epm_core.py:10394-10404](../../../epm_core.py#L10394)) como cada prueba de la suite construyen `ProjectInput(execution_year=..., costs=..., ...)` en una sola llamada — `__post_init__` corre en ese momento y captura el valor correcto, antes de que ningún bucle de escaneo del MILP reasigne `execution_year` sobre el objeto ya construido.

## 4. Valoración (`_evaluate_inner`)

El bloque de CAPEX (hoy en [epm_core.py:4155-4180](../../../epm_core.py#L4155-L4180)) se bifurca:

```python
if project.costs.capex_by_year:
    # Perfil explícito, pesos nominales ya inflados por el analista.
    # offset_k = año_k − declared_execution_year: la FORMA del perfil, fija
    # desde que se declaró — no cambia entre candidatos.
    #
    # El desplazamiento temporal usa deferral_years, EXACTAMENTE como el
    # CAPEX legado (rama else, abajo) y como los otros tres componentes de
    # la ecuación (beneficios, riesgo, CoD) — es la única referencia
    # temporal compartida de toda la evaluación, relativa al año base del
    # plan. execution_year NO participa en esta fórmula.
    #
    # CORRECCIÓN (hallada en la revisión de calidad de la Tarea 2 de
    # implementación): la primera versión de este diseño usaba
    # delta = execution_year − declared_execution_year en vez de
    # deferral_years, razonando que sumar deferral_years duplicaría el
    # desplazamiento. Error: EnterpriseOptimizer reasigna execution_year AL
    # CANDIDATO Y ADEMÁS pasa su propio deferral_years (relativo al año más
    # temprano del plan) — son dos referencias distintas que solo coinciden
    # por casualidad. Usar delta no evitaba un doble conteo; sustituía la
    # referencia correcta (deferral_years, compartida por toda la ecuación)
    # por una incorrecta (delta, aislada solo en esta rama). Caso verificado:
    # un proyecto declarado en 2029 evaluado como candidato en el año base
    # del plan (deferral_years=0) daba capex_pv 31% por encima del nominal
    # en vez de exactamente el nominal.
    capex = sum(project.costs.capex_by_year.values())  # nominal, para proporcionalidad
    capex_pv_por_tramo = {
        year: monto / ((1.0 + self.wacc) ** ((year - project.declared_execution_year) + deferral_years))
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

Se reporta `capex_schedule_pv_COP: dict[int, float]` (el `capex_pv_por_tramo` de arriba, con año calendario como clave en ambas ramas) junto a los campos existentes `capex_COP` y `capex_pv_COP`, para que la auditoría pueda ver dónde quedó cada peso.

**Nota de semántica de la clave (hallada en revisión de calidad):** las claves de la rama `capex_by_year` son los años **tal como se declararon**, no el año en que cada tramo aterriza en el escenario evaluado — `_evaluate_inner()` no conoce el año base del plan (solo `deferral_years`, un conteo de años, no un año calendario), así que no puede calcular un año de aterrizaje absoluto. La rama legada sí usa un año de escenario real (`execution_year`) como clave. Las dos ramas no son directamente comparables por la clave; se documenta así deliberadamente en vez de forzar una reanclaje que el motor no tiene información para calcular correctamente — no hay ningún consumidor de este campo todavía que dependa de lo contrario.

## 5. Presupuesto (ambos optimizadores MILP)

**Optimizador simple** ([epm_core.py:5510-5523](../../../epm_core.py#L5510-L5523)): hoy, cuando hay `capex_by_year`, busca coincidencia literal de año calendario (`bp.year in capex_by_year`) sin reanclar. Se reemplaza por:

```python
capex_by_year = getattr(proj.costs, 'capex_by_year', {})
declared = proj.declared_execution_year or proj.execution_year
for t, bp in enumerate(budget_periods):
    if capex_by_year:
        delta = proj.execution_year - declared  # 0 salvo que el bucle de abajo ya haya reasignado execution_year
        for year, monto in capex_by_year.items():
            landing_year = year + delta
            if landing_year == bp.year:
                capex_matrix[i, t] += monto
    else:
        cy = max(1, proj.construction_years)
        start_t = year_map.get(proj.execution_year, 0)
        if start_t <= t < start_t + cy:
            capex_matrix[i, t] = capex_arr[i] / cy
```

Nota de implementación: este bucle construye `capex_matrix[i, t]` **antes** de que el bucle de evaluación de VNT (líneas 5525-5530 actuales) reasigne `proj.execution_year` candidato por candidato — así que, tal como está hoy, `capex_matrix` se calcula una sola vez con `proj.execution_year` en su valor declarado, no por cada candidato. Para que el perfil también se reancle al probar cada año candidato (el mismo requisito que en valoración), el cálculo de `capex_matrix[i, t]` para proyectos con `capex_by_year` debe moverse **dentro** del bucle que ya itera candidatos (líneas 5525-5530), evaluando el `delta` con `proj.execution_year` ya reasignado a cada `bp.year` candidato — igual que ya hace el bucle de VNT inmediatamente después. El plan de implementación detalla esta fusión de bucles.

**Optimizador de alcance empresarial** ([epm_core.py:6941-6965](../../../epm_core.py#L6941-L6965)): aquí el bucle **ya** itera por año candidato (`period.year`) y ya reasigna `proj.execution_year = period.year` en cada iteración antes de valorar — el problema es más simple de corregir, solo hay que reanclar `capex_by_year` con el mismo patrón:

```python
cap_by_year = getattr(proj.costs, 'capex_by_year', {}) or {}
declared = proj.declared_execution_year or proj.execution_year
for t, period in enumerate(periods):
    if cap_by_year:
        delta = period.year - declared
        for year, monto in cap_by_year.items():
            landing_year = year + delta
            t_landing = year_idx.get(landing_year)
            if t_landing is not None:
                capex_matrix[i][t_landing] += monto
    else:
        cy = max(1, proj.construction_years)
        capex_matrix[i][t] = cap_total / cy
```

**Sobre `deferral_years` en este optimizador:** el bucle ya calcula `deferral = max(0, period.year - min(years))` y lo pasa a `engine.evaluate(proj, deferral_years=deferral)` ([epm_core.py:6959-6961](../../../epm_core.py#L6959-L6961)) — esto ya es consistente con la fórmula corregida de la sección 4, que también usa `deferral_years`. No requiere cambio adicional: al reasignar `execution_year` Y pasar `deferral_years` calculado desde `min(years)`, la valoración ya recibe la referencia temporal correcta.

**Corrección de alcance para el optimizador simple, hallada junto con el defecto de la sección 4:** el bucle de evaluación de VNT de `PortfolioOptimizer` ([epm_core.py:5525-5530](../../../epm_core.py#L5525-L5530)) reasigna `proj.execution_year` por candidato pero **nunca pasa `deferral_years`** — llama `self.engine.evaluate(proj)` a secas, que por defecto usa `deferral_years=0` siempre, sin importar qué candidato se esté probando. Se verificó empíricamente: con esta llamada, el VNT de un proyecto con CAPEX legado (sin `capex_by_year`) es prácticamente idéntico entre años candidatos (variación de ~0,1-0,2% por año, no un descuento económico real) — un límite preexistente de este optimizador, independiente de todo este diseño. Bajo la fórmula corregida de la sección 4, esto tiene una consecuencia directa: un proyecto con `capex_by_year` tampoco se reanclaría al probar candidatos distintos en este optimizador, porque `deferral_years` seguiría fijo en 0.

La corrección es la misma para ambos casos (CAPEX legado y `capex_by_year`): el bucle debe calcular `deferral_years = max(0, bp.year - base_year)` (mismo patrón que ya usa `EnterpriseOptimizer`, reutilizando el parámetro `base_year` que `optimize()` ya recibe) y pasarlo a `self.engine.evaluate(proj, deferral_years=deferral_years)`. Esto también hace que el CAPEX legado se vuelva sensible al año candidato en este optimizador — un cambio de comportamiento real, pero corrige un defecto preexistente (el escaneo de año óptimo era económicamente casi inerte) en vez de introducir uno nuevo. `PortfolioOptimizer` no tenía pruebas antes de este plan, así que no hay ninguna regresión que romper.

Un tramo cuyo `landing_year` cae fuera de la ventana de planeación presupuestal vigente no consume cupo de ESTE plan — pero ya se valoró completo en el VNT (sección 4).

## 6. Preselección por EAV dentro de grupos de exclusión

Nuevo paso, ejecutado antes de construir las matrices de ambos optimizadores:

```python
def _preselect_by_eav(projects, engine):
    """Para cada exclusion_group_id con más de una alternativa, se queda solo
    la de mayor Valor Anual Equivalente — salvo que una de las alternativas
    sea no-discrecional, en cuyo caso esa gana sin importar su EAV.

    Las alternativas descartadas no desaparecen: se reportan explícitamente
    con la razón ("descartada por EAV frente a <id_ganador>"), igual que hoy
    se reportan los proyectos postergados por falta de cupo presupuestal.
    """
    grupos = agrupar_por(projects, "exclusion_group_id")
    ganadores = [p for p in projects if not p.exclusion_group_id]
    descartados = []
    for gid, alternativas in grupos.items():
        if len(alternativas) == 1:
            ganadores.append(alternativas[0])
            continue
        mandatorias = [p for p in alternativas if p.is_mandatory]
        if len(mandatorias) > 1:
            raise ValueError(  # mismo error que ya existe hoy en R3d, sin cambios
                f"Grupo de exclusión '{gid}': hay {len(mandatorias)} alternativas "
                f"marcadas como no-discrecionales. Solo una puede ejecutarse."
            )
        ganador = mandatorias[0] if mandatorias else max(
            alternativas, key=lambda p: engine.evaluate(p)["eav_COP"]
        )
        ganadores.append(ganador)
        descartados += [(p, ganador) for p in alternativas if p is not ganador]
    return ganadores, descartados
```

Notas de diseño:
- Los proyectos sin `exclusion_group_id` no se tocan.
- La restricción de exclusión mutua R3d **se conserva** dentro del MILP como salvaguarda — no se retira solo porque la preselección ya resolvió el caso normal.
- Si la alternativa ganadora por EAV no cabe en ningún año del plan de capital vigente, el MILP la posterga — igual que hoy posterga cualquier proyecto sin cupo. La preselección **no** sustituye automáticamente por una alternativa más barata del mismo grupo aunque esa sí quepa.
- Regla de precedencia: lo obligatorio (`is_mandatory`) pesa más que el EAV.

## 7. Manejo de errores y casos de borde

| Caso | Comportamiento |
|---|---|
| `capex_by_year` con año anterior a `declared_execution_year` | Permitido — representa un tramo ya incurrido antes del arranque formal (p. ej. estudios/diseño); su offset es negativo y se descuenta hacia adelante en el tiempo (factor > 1), consistente con la fórmula |
| Suma del perfil = 0 | Misma alerta pedagógica que hoy da `capex_COP = 0` |
| Alternativa obligatoria con EAV menor que otra del grupo | Gana la obligatoria |
| Dos alternativas obligatorias en el mismo grupo | `ValueError` — mismo comportamiento que existe hoy en R3d, sin cambios |
| `landing_year` de un tramo cae después de la ventana de presupuesto | No consume cupo de este plan; sí se valora completo en el VNT |
| Alternativa ganadora por EAV sin cupo presupuestal en ningún año | Se posterga, como cualquier proyecto sin cupo hoy — no hay sustitución automática dentro del grupo |
| `declared_execution_year` declarado explícitamente distinto de `execution_year` al construir el proyecto | Se respeta el valor declarado — `__post_init__` solo lo autocompleta cuando quedó en 0 |

## 8. Pruebas

Nuevo grupo `S31_PerfilCapexPorAnio` (sección 4-5 de este spec) y `S32_PreseleccionPorEAV` (sección 6), en `test_suite_epm_full.py`, siguiendo la disciplina de mutación de los grupos S23-S30 (cada guardián reconstruye el valor esperado con un cálculo independiente, no confía en el campo que el propio motor reporta):

**S31 — Perfil de CAPEX por año:**
1. `capex_by_year` con un solo tramo en `declared_execution_year` ⇒ resultado idéntico, al peso, al camino legado con `deferral_years=0` (regresión).
2. Perfil de N tramos, sin escaneo (`execution_year == declared_execution_year`) ⇒ VNT reconstruido con un oráculo independiente que descuenta cada tramo por `año − declared_execution_year`.
3. Perfil de N tramos, **con** escaneo (`execution_year` reasignado a un candidato distinto de `declared_execution_year`, como hace el MILP) ⇒ el perfil se reancla — cada tramo aterriza `delta` años más adelante o más atrás, verificado con oráculo independiente.
4. Presupuesto (ambos optimizadores): un tramo reanclado que cae dentro de la ventana de planeación consume cupo del año de calendario correcto; uno fuera de la ventana no consume cupo pero sí entra completo al VNT.
5. `declared_execution_year` no declarado explícitamente ⇒ `__post_init__` lo autocompleta con `execution_year`, verificado con una construcción directa de `ProjectInput`.

**S32 — Preselección por EAV:**
6. **Prueba decisiva:** un grupo de exclusión con una alternativa de vida larga y VNT crudo mayor, contra una de vida corta con EAV mayor ⇒ la preselección elige la de EAV mayor. Esta prueba debe **fallar contra el motor actual** — es la que demuestra que la brecha original quedó cerrada.
7. Alternativa obligatoria con EAV menor que otra del grupo ⇒ gana la obligatoria.
8. Dos alternativas obligatorias en el mismo grupo ⇒ `ValueError`.

**Ambos grupos:** campaña de mutación dedicada (`mutacion_capex_perfil.py` para S31, `mutacion_preseleccion_eav.py` para S32) sobre las ediciones de este trabajo, antes de dar el trabajo por cerrado — mismo método que las ocho campañas anteriores: copiar a carpeta aislada, mutar un literal verificado único, correr la suite, confirmar MUERTO en cada mutación.

## 9. Decisiones registradas (para no re-discutirlas en la implementación)

| Decisión | Elegida | Alternativas consideradas |
|---|---|---|
| Formato del perfil de CAPEX | Extender `capex_by_year` existente (año calendario → monto) | Crear un campo nuevo `capex_schedule` de offset relativo, en paralelo |
| Denominación de los montos | Nominales, ya inflados por el analista | Pesos de hoy, inflados tramo por tramo por el motor |
| Anclaje temporal del perfil | Se reancla al año candidato durante el escaneo, vía `declared_execution_year` | Queda fijo en el calendario declarado (tratamiento tipo `is_committed`) |
| Brecha de valoración de `capex_by_year` (ya en producción vía importador) | Se corrige en este mismo plan | Registrar aparte y decidir prioridad después |
| Rol del EAV en la decisión | Preselecciona dentro de `exclusion_group_id`; VNT/CAPEX real sigue gobernando el MILP y el presupuesto | EAV reemplaza VNT en todo el motor; EAV solo informativo |
| EAV vs. restricción de presupuesto | Preselección dura — solo la de mayor EAV entra al MILP, se pospone si no cabe | Todas compiten con EAV como bono en el objetivo |
