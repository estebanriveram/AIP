# Reto 6 — Reputación: Diseño y Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar un sexto Value Driver (VD-6, Reto "Reputación") al marco de valor, reactivando la calibración de VD-1-F (retirada en config_v3.0.0) y retirando el componente reputacional de las anclas de consecuencia de la matriz de riesgo, para que un mismo evento no se cuente dos veces en la ecuación del VNT.

**Origen y decisiones ya tomadas en conversación (no se re-preguntan en este plan):**
- EPM decidió elevar Reputación a Reto de pleno derecho, uniforme para los 4 negocios, sin definición propia — la metodología de cálculo sigue siendo la de Copperleaf/VDM.
- **Hallazgo llevado al usuario y decisión tomada:** Copperleaf documenta la reputación como categoría de RIESGO (DOC-RISK §3.3: *"Assets can carry multiple types of risk (e.g. reliability, safety, environmental, reputation, etc.)"*), no como Modelo de Valor positivo — el diseño anterior de EPM (reputación colapsada en las anclas L1-L5) era, en ese sentido, más fiel al documento de referencia. El usuario decidió explícitamente seguir adelante con VD-6 de todas formas, entendiendo que es una construcción propia de EPM, no algo que Copperleaf documente como Modelo de Valor — **esta distinción debe quedar registrada, no se resuelve en silencio.**
- **Decisión de arquitectura:** VD-6 reemplaza el componente reputacional del riesgo — no coexisten. La matriz de riesgo deja de cobrar `reputacional_COP` en `value_COP`; VD-6 pasa a ser el único lugar donde la reputación entra al VNT.
- VD-1-F (`type: concept_rating`, curva-S, `scale_max_by_bu` ya calibrado para los 5 alcances TyD/AyA/Generación/Gas/Todos) ya mide exactamente "NPS + CX + imagen" — es la calibración correcta para reactivar como VD-6-A, sin inventar números nuevos.

**Tech Stack:** Python 3, `dataclasses`, JSON (config), `unittest`.

**Spec de referencia:** ninguna nueva — este plan documenta su propio diseño, dado el tamaño acotado del cambio (config + una línea de comentario en Python + pruebas). No amerita un documento de spec separado.

---

## Diseño

### 1. Matriz de riesgo — retirar el componente reputacional de `value_COP`

`config.json → risk_matrix.consequence_levels`: en cada uno de los 5 niveles, `value_COP` es un número **precalculado y almacenado** (no se suma en tiempo de ejecución desde `_componentes` — verificado: el motor solo lee `value_COP`, nunca `_componentes`, que es puramente documental). Se recalcula cada `value_COP` como `directo_COP + regulatorio_COP` (se retira `reputacional_COP` de la suma, pero se conserva el campo dentro de `_componentes` como registro histórico, con una nota explicando por qué ya no se suma).

| Nivel | `value_COP` hoy | `value_COP` nuevo |
|---|---|---|
| L1 | 80.000.000 | 55.000.000 |
| L2 | 400.000.000 | 250.000.000 |
| L3 | 2.000.000.000 | 1.300.000.000 |
| L4 | 12.000.000.000 | 7.000.000.000 |
| L5 | 50.000.000.000 | 25.000.000.000 |

### 2. VD-1-F — se conserva deprecada, pero con la razón actualizada

Sigue existiendo dentro de `VD-1.sub_criteria` (no se borra — conserva trazabilidad de evaluaciones históricas que la referencian por id), sigue con `deprecated: true`, pero `deprecated_reason` cambia: ya no dice "duplicaría el componente reputacional de las anclas de riesgo" (esa razón deja de ser cierta una vez se retira el componente reputacional del riesgo) — dice que su contenido se trasladó a VD-6-A, y que usar ambos duplicaría el aporte reputacional **dentro de los value drivers**, no ya frente al riesgo.

`epm_core.py`, en `_calc_driver()`, el bloqueo (`_DEPRECATED_SC = {"VD-1-F"}`) se conserva sin cambios de lógica — solo se actualiza el comentario que lo justifica, por la misma razón de arriba.

### 3. VD-6 — nuevo Value Driver

Nueva entrada en `config.json → value_drivers`, después de VD-5:

```json
{
  "id": "VD-6",
  "reto": "Reputación",
  "weight": 1.0,
  "sub_criteria": [
    {
      "id": "VD-6-A",
      "label": "Reputación EPM (NPS + CX + imagen)",
      "type": "concept_rating",
      "weight": 1.0,
      "value_function": "s_curve",
      "vf_inflection": 5.0,
      "vf_steepness": 1.5,
      "scale_max_COP": 320000000000,
      "scale_max_by_bu": {
        "TyD": 280000000000,
        "AyA": 320000000000,
        "Generacion": 380000000000,
        "Gas": 180000000000,
        "Todos": 420000000000
      },
      "_fuente": "Reactivado de VD-1-F (retirado en config_v3.0.0). Calibración original conservada sin cambios: 20% ingreso regulado anual × factor exposición reputacional.",
      "_nota": "Reto agregado por EPM sin definición propia — uniforme para los 4 negocios, metodología heredada de VD-1-F/Copperleaf VDM. Ver REGISTRO_ASUNTOS_ABIERTOS.md D-11 para la distinción metodológica frente a la fuente Copperleaf (que documenta reputación como categoría de riesgo, no como Modelo de Valor)."
    }
  ]
}
```

**No requiere entrada en `bu_driver_config`** — verificado: ese diccionario solo lo consumen sub-criterios `monetizable` (precio por unidad varía por negocio); los `concept_rating` resuelven su variación por negocio directamente desde `scale_max_by_bu` en la propia entrada de `value_drivers`, leído únicamente por `ProjectWizard._capture_sc()` (línea ~5248) — el mismo mecanismo que ya usaba VD-1-F, sin cambios de código necesarios ahí.

---

## Tareas

### Task 1: Config — matriz de riesgo, VD-1-F, VD-6

**Files:**
- Modify: `config.json` (`risk_matrix.consequence_levels`, `value_drivers[VD-1].sub_criteria[VD-1-F].deprecated_reason`, nueva entrada `value_drivers` para VD-6)
- Modify: `epm_core.py` (comentario en `_calc_driver()`, localizar por el texto `_DEPRECATED_SC = {"VD-1-F"}`)
- Test: `test_suite_epm_full.py`

- [ ] **Step 1: Escribir las pruebas que fallan**

```python
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

    def test_matriz_riesgo_ya_no_cobra_componente_reputacional(self):
        """value_COP de cada nivel de consecuencia debe ser directo+regulatorio,
        sin el reputacional que ahora vive solo en VD-6."""
        niveles_esperados = {
            "1": 55_000_000, "2": 250_000_000, "3": 1_300_000_000,
            "4": 7_000_000_000, "5": 25_000_000_000,
        }
        anclas = self.cfg["risk_matrix"]["consequence_levels"]
        for nivel, esperado in niveles_esperados.items():
            componentes = anclas[nivel]["_componentes"]
            reconstruido = componentes["directo_COP"] + componentes["regulatorio_COP"]
            self.assertEqual(reconstruido, esperado,
                             f"Nivel {nivel}: directo+regulatorio no coincide con el "
                             f"value_COP esperado — revisar si _componentes cambió.")
            self.assertEqual(anclas[nivel]["value_COP"], esperado,
                             f"Nivel {nivel}: value_COP todavía incluye el componente "
                             f"reputacional o no se actualizó.")
```

(La clase donde viven estas pruebas: agrégalas junto a `test_vd1f_esta_bloqueado_y_no_solo_es_pequeno`, en la misma clase `S1_ValueEngine` — usa `self.cfg`/`self.engine` ya provistos por su `setUp`.)

- [ ] **Step 2: Confirmar que fallan**

Run: `python -m unittest test_suite_epm_full.S1_ValueEngine.test_vd6_reputacion_se_calcula_como_concept_rating test_suite_epm_full.S1_ValueEngine.test_vd6_no_esta_bloqueado_a_diferencia_de_vd1f test_suite_epm_full.S1_ValueEngine.test_matriz_riesgo_ya_no_cobra_componente_reputacional -v`
Expected: las tres FALLAN (VD-6 no existe en `value_drivers` todavía — pero como estas pruebas construyen `SubCriterionInput`/`DriverInput` directamente, sin pasar por `config["value_drivers"]`, las dos primeras en realidad prueban el MOTOR, no el config — deberían pasar ya, porque `_calc_driver`/`_DEPRECATED_SC` no distinguen "VD-6" de cualquier otro id no bloqueado. Confirma este comportamiento: si las dos primeras ya pasan antes de tocar código, es evidencia correcta de que el motor no necesita cambios — el `_DEPRECATED_SC` es específico al id `"VD-1-F"`, no una lista blanca. Solo la tercera prueba (matriz de riesgo) debe fallar de verdad contra el `config.json` sin modificar.)

- [ ] **Step 3: Actualizar `config.json`**

3a. `risk_matrix.consequence_levels` — para cada nivel 1-5, cambiar `value_COP` al valor de la tabla de la sección "Diseño" arriba, y agregar dentro de `_componentes` (o como hermano de `_nota`) una nota explicando el cambio:

```json
"_nota_reputacion": "value_COP ya NO incluye reputacional_COP desde que Reputación se elevó a VD-6 (Reto propio) — se conserva reputacional_COP aquí solo como registro histórico de la calibración v3.0. Sumarlo de nuevo a value_COP duplicaría el aporte reputacional frente a VD-6."
```

3b. `value_drivers[VD-1].sub_criteria[VD-1-F].deprecated_reason` — reemplazar:

```
"El riesgo reputacional está ahora incorporado en las anclas L1-L5 de la matriz de consecuencias (metodología Copperleaf C55). Usar VD-1-F Y la matriz de riesgos sería doble conteo. Conservado para trazabilidad de evaluaciones históricas."
```

por:

```
"Contenido trasladado a VD-6-A (Reto Reputación, agregado por EPM). Usar VD-1-F junto con VD-6-A duplicaría el aporte reputacional dentro de los value drivers. Ya NO es sobre la matriz de riesgo — el componente reputacional se retiró de las anclas L1-L5 al mismo tiempo que se creó VD-6. Conservado para trazabilidad de evaluaciones históricas anteriores a este cambio."
```

3c. Agregar la nueva entrada `value_drivers` para VD-6 (bloque completo en la sección "Diseño" arriba), después de la entrada VD-5.

- [ ] **Step 4: Actualizar el comentario en `epm_core.py`**

Localizar por el texto `_DEPRECATED_SC = {"VD-1-F"}` dentro de `_calc_driver()`. El bloque de comentario que lo precede hoy dice:

```python
        # Sub-criterios retirados: la reputación ya está colapsada en
        # las anclas L1-L5 (value_COP = directo + regulatorio + reputacional).
        # Usar VD-1-F duplica el componente reputacional. El motor lo bloquea.
        _DEPRECATED_SC = {"VD-1-F"}
```

Reemplazar el comentario (la línea de código no cambia) por:

```python
        # Sub-criterios retirados: VD-1-F medía reputación dentro de VD-1;
        # su contenido se trasladó a VD-6 (Reto Reputación, agregado por
        # EPM). Usar VD-1-F duplicaría el aporte reputacional frente a
        # VD-6-A. El motor lo bloquea. Nota: ya NO es sobre la matriz de
        # riesgo — el componente reputacional se retiró de las anclas
        # L1-L5 al crear VD-6, así que esa duplicación específica ya no
        # aplica; la que sí aplica es VD-1-F vs. VD-6-A.
        _DEPRECATED_SC = {"VD-1-F"}
```

- [ ] **Step 5: Actualizar el docstring de la prueba existente sobre VD-1-F**

En `test_suite_epm_full.py`, `test_vd1f_esta_bloqueado_y_no_solo_es_pequeno` (clase donde vive `S25_FuncionDeValor` o la que corresponda — localizar por el nombre del método) — su docstring dice hoy *"VD-1-F duplicaría el componente reputacional ya colapsado en las anclas de riesgo"*. Actualizar a *"VD-1-F duplicaría el aporte reputacional que ahora vive en VD-6-A — ya no es sobre las anclas de riesgo, que dejaron de cobrar componente reputacional al crear VD-6."* Sin cambiar la aserción — el mecanismo de bloqueo sigue siendo el mismo, solo cambia por qué existe.

- [ ] **Step 6: Confirmar que todas pasan**

Run: `python -m unittest test_suite_epm_full.S1_ValueEngine -v` (o la clase donde se agregaron las pruebas nuevas) — todas OK.

- [ ] **Step 7: Correr toda la suite**

Run: `python test_suite_epm_full.py`
Expected: `OK`, sin regresiones. Ningún proyecto existente en la suite usa VD-6 ni depende del valor exacto de `reputacional_COP` en la matriz de riesgo — pero **verificar explícitamente** que ninguna prueba de riesgo (grupos S1, S21, S22 y las de integración) tenía un valor esperado hardcodeado que dependiera del `value_COP` viejo de algún nivel de consecuencia; si alguna lo tenía, es una prueba legítimamente rota por este cambio (no un defecto oculto) y su valor esperado debe actualizarse a la nueva tabla, documentando por qué en el mismo commit.

- [ ] **Step 8: Commit**

```bash
git add config.json epm_core.py test_suite_epm_full.py
git commit -m "feat: Reto 6 — Reputación (VD-6), retira componente reputacional del riesgo

Reactiva la calibración de VD-1-F (retirado en config_v3.0.0) como VD-6,
nuevo Value Driver agregado por EPM, uniforme para los 4 negocios. La
matriz de riesgo deja de cobrar el componente reputacional en value_COP
de cada nivel de consecuencia (L1-L5) — Reputación pasa a vivir
únicamente en VD-6, evitando doble conteo. VD-1-F se conserva deprecado
para trazabilidad histórica, con la razón de bloqueo actualizada.

Hallazgo documentado en el registro: la fuente Copperleaf (DOC-RISK
§3.3) trata la reputación como categoría de riesgo, no como Modelo de
Valor — VD-6 es una decisión propia de EPM, no algo que Copperleaf
documente. Decisión tomada explícitamente por el usuario."
```

---

### Task 2: Registro

**Files:**
- Modify: `REGISTRO_ASUNTOS_ABIERTOS.md`

- [ ] **Step 1: Agregar la entrada D-11**

Documentar: el Reto 6 agregado por EPM, la reactivación de VD-1-F como VD-6-A, el retiro del componente reputacional de la matriz de riesgo, y — explícitamente, sin suavizarlo — el hallazgo de que Copperleaf documenta reputación como categoría de riesgo (DOC-RISK §3.3), no como Modelo de Valor, y que VD-6 es una decisión de EPM que se aparta de esa referencia, tomada conscientemente por el usuario tras conocer el hallazgo. Referenciar el número de pruebas antes/después.

- [ ] **Step 2: Commit**

```bash
git add REGISTRO_ASUNTOS_ABIERTOS.md
git commit -m "docs: registrar D-11 — Reto 6 Reputación"
```

---

## Fuera de alcance de este plan

La "biblioteca de modelos de valor" (motor genérico de cuestionario + fórmula, al estilo Copperleaf Value Model Library) es un proyecto de arquitectura aparte, de tamaño comparable a los planes de perfil de CAPEX o preselección por EAV — no se diseña ni se toca aquí. VD-6 usa el arquetipo `concept_rating` que ya existe, sin necesitarla.

---

## Estado final — ejecutado, con alcance ampliado tres veces

El detalle completo, incluido el error propio y su corrección, vive en `REGISTRO_ASUNTOS_ABIERTOS.md` D-11 — este resumen solo apunta ahí en vez de duplicar la narrativa.

- **Task 1 original** (matriz de riesgo plana, VD-1-F, VD-6): commit `7ac9161`.
- **Ampliación 1** (encontrada en revisión de calidad): `config.json` nunca definía `risk_matrix.consequence_levels_by_bu` — la tabla que el motor realmente resuelve para los 4 negocios reales. Se agregó, con el componente reputacional estimado (no calibrado directamente — pendiente validar con Gestión del Riesgo EPM), se sincronizó `DEFAULT_CONFIG`, y se hizo VD-6-A alcanzable desde `ProjectWizard`. Commit `446dd0d`.
- **Ampliación 2** (encontrada en la misma revisión): la ampliación 1 rompió dos pruebas del grupo S21. Diagnóstico sistemático descartó el defecto histórico como causa. Commit `446dd0d` incluyó una primera corrección que, verificado por revisión de calidad posterior, dejaba de detectar ese mismo defecto histórico si se reintrodujera — un error propio, corregido en el commit `179f08c` reduciendo CAPEX/beneficio en vez de subir la severidad de riesgo, con dos guardianes permanentes nuevos (meta-prueba de detección del defecto, prueba de sincronía `config.json`/`DEFAULT_CONFIG`).
- **Task 2** (registro D-11): incorporado en los commits de arriba, no como commit separado.

Suite: 265 → 271 pruebas (+6). Sin campaña de mutación dedicada — el trabajo es mayormente configuración + guardianes verificados por reintroducción directa del defecto real, no un mecanismo nuevo del motor que amerite su propio script de mutación.
