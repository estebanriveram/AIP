# Componentes múltiples de inacción, Registro de Activos y horizonte único de análisis

**Fecha:** 2026-09-04
**Estado:** Diseño aprobado por el usuario, pendiente de plan de implementación
**Origen:** Preguntas directas del usuario en los talleres de calibración de AyA: *"¿no está la opción mixta?"* y *"¿no podían existir varios activos degradándose [en un mismo proyecto]?"* — verificado contra el código que ninguna de las dos existe hoy.

## 1. Problema

Se identificaron dos brechas relacionadas, encontradas al preparar el informe de trazabilidad de riesgo para AyA (entregado como documento Word/Artifact fuera de este repositorio, no como spec):

1. **No hay paso de clasificación explícito.** El asistente interactivo tiene dos clasificaciones que corren en paralelo sin conectarse: "Tipo de Inversión" (Reposición de Activos / Crecimiento-Expansión desde D-13) solo alimenta qué Retos de valor se sugieren ([epm_core.py:1285](../../../epm_core.py#L1285)); "Familia de Activo" decide qué rama de inacción se muestra (matriz genérica / curva física), sin relación con lo que el analista declaró como tipo de inversión. Un proyecto de expansión termina, dos pasos después, llenando parámetros de una curva de deterioro físico como si hubiera un activo envejeciendo.

2. **La arquitectura es de un solo componente por proyecto.** `ProjectInput.risk: Optional[RiskInput]` ([epm_core.py:2103](../../../epm_core.py#L2103)) admite cero o **uno**. El "modo directo" (costo de expansión, `RiskInput.baseline_annual_COP`), cuando está activo, **reemplaza por completo** cualquier cálculo físico — no lo complementa ([epm_core.py:3888](../../../epm_core.py#L3888), comentario: *"un costo anual conocido que crece a una tasa NO es una curva de envejecimiento"*). No existe forma de que un proyecto agregue el riesgo de varios activos físicos distintos, ni de mezclar un componente físico con uno de costo directo.

Verificado contra Copperleaf real (no contra documentación propia — ver §9, primera fila) antes de diseñar la corrección: su RFI a EPM confirma explícitamente *"los usuarios pueden asociar **uno o más activos** específicos del registro de activos con **una inversión**, lo que incluye todas las Medidas de Valor (riesgos...) asociadas a dichos activos"*, y que *"el impacto de esa inversión es la **agregación** de todos los riesgos mitigados por esa inversión"*. El diseño de este documento replica ese patrón — no es una idea nueva, es cerrar una brecha frente al propio proveedor que EPM evaluó.

## 2. Alcance

Dentro de alcance:
- `ProjectInput.risk_components: List[RiskInput]` reemplaza `risk: Optional[RiskInput]` — reemplazo limpio, sin campo paralelo de compatibilidad (decisión explícita del usuario, ver §9).
- Horizonte de análisis único por proyecto, declarado por el analista (campo numérico en blanco, sin sugerencia precargada), que alimenta beneficios, cada componente de riesgo, EAV, VEI y Costo de Posponer — reemplaza `get_horizon_for_asset()` para estos cuatro usos.
- Registro de Activos — núcleo mínimo: alta manual, importación CSV/Excel, búsqueda simple, asociación de un activo registrado a un componente de inacción. Reutiliza `AssetProfile` ([epm_core.py:8928](../../../epm_core.py#L8928)) sin cambios de campos.
- Integración en el asistente: sección "Componentes de Inacción" reemplaza el paso "Do Nothing" actual; declaración explícita obligatoria por componente × alternativa (sin default silencioso).
- "Familia de Activo" en el Paso 1 del asistente queda condicionada a `Tipo de Inversión == "Reposición de Activos"`.

Fuera de alcance (no se toca en este diseño):
- Búsqueda avanzada con operadores AND/OR, integración GIS, archivos adjuntos, comentarios por activo — capacidades reales de Copperleaf (verificadas en su RFI) que el usuario decidió no replicar (núcleo mínimo).
- El horizonte por tipo de activo (`asset_horizons_by_bu`, `get_horizon_for_asset()`) **no se elimina** — sigue en uso, sin cambios, por el panel de exploración de curvas de degradación en la pestaña Auditoría ("Vista de Flota"), que es una herramienta distinta de este flujo.
- No se construye ningún mecanismo para que la condición (`AssetProfile.current_condition`, escala 0-1) alimente el cálculo de riesgo del componente — ver nota de no-conexión en §3.
- No se reformula la forma de ninguna fórmula de valor (Retos, matriz de riesgo) — ese es alcance de la fábrica de software, ya comunicado aparte.
- Los 6 tipos de inversión ya se redujeron a 2 en D-13, previo a y independiente de este diseño.
- `ProjectInput.asset_type` queda sin ningún consumidor dentro de este flujo (los cuatro usos de `get_horizon_for_asset` que antes dependían de él se reemplazan en §5; `_get_risk_growth_rate` pasa a leer el `asset_type` de cada componente, §3.1-§4, no el del proyecto). El campo no se elimina — no se auditó si algo fuera de este flujo todavía lo lee — solo queda vestigial para este propósito específico.

## 3. Modelo de datos

### 3.1 `RiskInput` — campos nuevos ([epm_core.py:2026](../../../epm_core.py#L2026))

```python
label: str = ""
"""Nombre del componente para el desglose (ej. 'Tubería sector norte',
'Costo directo expansión') — sin esto, un desglose de varios componentes
no significa nada para quien lo lee."""

asset_type: str = ""
"""Reemplaza la dependencia de ProjectInput.asset_type para este
componente — cada componente puede ser un tipo de activo distinto (o
ninguno, si es costo directo). Único consumidor: _get_risk_growth_rate()
por componente (§4)."""

source_asset_id: str = ""
"""Referencia al activo del Registro (§6) del que se copiaron los valores
de este componente, si vino de ahí. NO es un enlace vivo — es trazabilidad
de origen ('esto salió del activo X, tal como estaba declarado el
[fecha]'). Editar el activo en el Registro después NO cambia este
componente ni ningún resultado ya calculado con él."""
```

**Nota de no-conexión, verificada, no asumida:** `AssetProfile.current_condition` (§6.1) no tiene destino en `RiskInput` — `_calc_risk()` ([epm_core.py:3799](../../../epm_core.py#L3799)) solo usa `asset_age_years` para la curva de Weibull, nunca un puntaje de condición 0-1. Capturar condición en el Registro es información de contexto para el analista, no un insumo del cálculo de riesgo multi-componente. Se documenta aquí para que nadie asuma lo contrario al usar el Registro.

### 3.2 `ProjectInput` — reemplazo de campo ([epm_core.py:2093](../../../epm_core.py#L2093))

```python
# Antes:
risk: Optional[RiskInput] = None

# Después:
risk_components: List[RiskInput] = field(default_factory=list)
"""Cero, uno o varios componentes de inacción — cada uno con su propia
clasificación (matriz genérica / curva física / costo directo), calculados
de forma independiente y sumados (§4). Reemplaza 'risk' — reemplazo
limpio, no queda un campo paralelo (ver §9)."""

analysis_horizon_years: int = 0
"""Horizonte único del proyecto, declarado por el analista — alimenta
beneficios, CADA componente de risk_components, EAV, VEI y Costo de
Posponer (§5). 0 = no declarado; el proyecto no puede evaluarse sin él
(ver §7)."""
```

Los 21 usos existentes de `project.risk` en `epm_core.py` se migran a iterar `project.risk_components` — no hay compatibilidad hacia atrás con el campo singular (decisión explícita, §9).

### 3.3 Registro — reutiliza `AssetProfile` sin cambios

`AssetProfile` ([epm_core.py:8928](../../../epm_core.py#L8928)) ya tiene todos los campos necesarios para el núcleo mínimo: `asset_id`, `asset_name`, `asset_class`, `business_unit`, `install_year`, `current_condition`, `replacement_cost_COP`, `annual_maintenance_COP`, `weibull_beta`, `weibull_eta`. Se agrega un solo campo:

```python
status: str = "activo"   # "activo" | "retirado"
"""D-14: soft-delete — ver §6.3. Nunca se borra un AssetProfile, se
retira. list() excluye retirados por defecto."""
```

## 4. Cálculo y agregación (`_evaluate_inner`)

Reemplaza el bloque actual ([epm_core.py:4174](../../../epm_core.py#L4174), `elif project.risk: risk_result = self._calc_risk(project.risk, ...)`):

```python
risk_components_results = []
delta_r_npv_total = 0.0
for component in project.risk_components:
    r = self._calc_risk(
        component,
        construction_years=eff_cy,
        horizon=project.analysis_horizon_years,   # §5 — único, no por componente
        business_unit=project.business_unit,
        risk_growth_rate=self._get_risk_growth_rate(
            project.business_unit, asset_type=component.asset_type),  # por componente
        deferral_years=deferral_years,
    )
    r["label"] = component.label
    r["source_asset_id"] = component.source_asset_id
    risk_components_results.append(r)
    delta_r_npv_total += r["delta_r_npv_COP"]

delta_r_npv = delta_r_npv_total
risk_result = {
    "total_delta_r_npv_COP": delta_r_npv_total,
    "components": risk_components_results,
}
```

`_calc_risk()` **no cambia internamente** — matriz genérica, curva física y modo directo siguen siendo exactamente la misma función, probada y calibrada; el único cambio es que ahora se invoca una vez por componente en lugar de una vez por proyecto. El "modo directo" deja de ser un caso especial: es un componente más de la lista, con las mismas reglas que cualquier otro.

`_dr_override` (`delta_r_override_COP`, portafolios no-Sostenimiento, [epm_core.py:4177](../../../epm_core.py#L4177)) no se toca — sigue siendo un mecanismo independiente que bypasea `risk_components` por completo, sin relación con este cambio.

## 5. Horizonte único de análisis

Validado contra dos fuentes reales de Copperleaf, no contra documentación propia (§9, primera fila):

- White paper *"VDM — Develop the Value Framework"* (Copperleaf, distribuido a EPM): *"Financial Parameters: ensure that organizations are applying a **uniform approach** to inflation, discount rates and **benefit horizons**"*.
- Página pública `copperleaf.com/why-copperleaf/finance/`: caso real de un plan de USD $2.000M evaluado *"over a 6-year planning horizon"* — un horizonte para todo el plan, no uno por activo.

`ProjectInput.analysis_horizon_years` (§3.2) reemplaza `get_horizon_for_asset(project)` en los cuatro puntos donde hoy se usa dentro de la valoración de un proyecto:

| Uso actual (`get_horizon_for_asset`) | Línea | Reemplazo |
|---|---|---|
| Horizonte de beneficios (`driver_results`) | [epm_core.py:4152](../../../epm_core.py#L4152) | `project.analysis_horizon_years` |
| Horizonte de riesgo (por componente, §4) | [epm_core.py:4192](../../../epm_core.py#L4192) | `project.analysis_horizon_years` |
| EAV / VEI | [epm_core.py:4303](../../../epm_core.py#L4303) | `project.analysis_horizon_years` |
| Costo de Posponer (`_calc_timing`) | [epm_core.py:4558](../../../epm_core.py#L4558) (llamada dentro de `_calc_timing`) | `project.analysis_horizon_years` |

`get_horizon_for_asset()` y `asset_horizons_by_bu` **no se eliminan** — quedan intactos para su único uso restante, ajeno a este flujo (Vista de Flota, Auditoría → Degradación Predictiva). `evaluate_marginal()` ([epm_core.py:3442](../../../epm_core.py#L3442), llamada en su interior) también migra a leer el horizonte del proyecto en lugar de llamar `get_horizon_for_asset`.

## 6. Registro de Activos

### 6.1 `AssetRegistryStore` — mismo patrón que `SnapshotStore` ([epm_core.py:9661](../../../epm_core.py#L9661))

```python
class AssetRegistryStore:
    def __init__(self, storage_dir: str = "./activos_registrados"): ...

    def create(self, asset: AssetProfile) -> None:
        """Falla si asset_id ya existe — sin sobrescritura silenciosa."""

    def update(self, asset: AssetProfile) -> None:
        """Falla si asset_id NO existe — simetría con create()."""

    def get(self, asset_id: str) -> Optional[AssetProfile]: ...

    def list(self, business_unit: str = None, asset_class: str = None,
             include_retired: bool = False) -> List[AssetProfile]: ...

    def search(self, query: str) -> List[AssetProfile]:
        """Substring simple en asset_id o asset_name — sin AND/OR (fuera de alcance, §2)."""

    def retire(self, asset_id: str) -> None:
        """status = 'retirado'. NO borra el archivo — ver §6.3."""

    def import_csv(self, path: str) -> Tuple[List[AssetProfile], List[dict]]:
        """(importados, errores_por_fila). Cada fila con asset_id ya
        existente es un error reportado, no una sobrescritura — misma
        regla que create()."""
```

Persistencia: una carpeta (`./activos_registrados/`), un archivo JSON por activo (`{asset_id}.json`), un `_index.json` para listar/buscar sin abrir cada archivo — exactamente el patrón ya usado y probado por `SnapshotStore`.

### 6.2 Alta manual y masiva

Formulario manual: los mismos campos de `AssetProfile` (§3.3) — Familia/Clase reutiliza el catálogo `ASSET_FAMILIES` ya existente; β/η se precargan desde la familia elegida (mismo mecanismo que ya usa el formulario Asset-Aware actual), editable.

Importación CSV/Excel: mismas columnas que el formulario, reutilizando el motor de detección flexible de columnas ya usado por el importador de portafolios ([epm_core.py:10254](../../../epm_core.py#L10254)) — sinónimos, insensible a mayúsculas/tildes. No se construye un parser nuevo.

### 6.3 Por qué `retire()` y no `delete()`

`SnapshotStore` es append-only por diseño, explícitamente por trazabilidad ISO 55001 §4.5 ([epm_core.py:9661](../../../epm_core.py#L9661), docstring: *"ningún snapshot puede eliminarse ni modificarse"*). Un activo que se reemplaza físicamente no debe desaparecer del Registro: si un proyecto pasado lo referenció (`RiskInput.source_asset_id`, §3.1), debe poder auditarse contra qué datos se evaluó. `retire()` marca `status="retirado"`; el archivo permanece.

### 6.4 Copia, no enlace vivo

Cuando un componente de inacción (§7) selecciona un activo del Registro, sus valores (edad, β/η) se **copian** a los campos del `RiskInput` en ese momento — no queda enlazado. Editar el activo en el Registro después no cambia resultados ya calculados. `source_asset_id` (§3.1) preserva de dónde salió el dato, sin crear una dependencia en vivo.

## 7. Integración en el asistente

- **Paso 1 (Contexto):** se agrega el campo **Horizonte de análisis** (numérico, en blanco, sin sugerencia — §5). El selector "Familia de Activo" solo se muestra si `Tipo de Inversión == "Reposición de Activos"` — para "Crecimiento / Expansión" no aplica.
- **Paso "Duelo" → sección "Componentes de Inacción"** (reemplaza el "Do Nothing" actual, en el mismo lugar del flujo, compartido entre todas las alternativas): alta/edición/eliminación de componentes; cada uno con Nombre + Tipo (Activo físico del Registro / Nivel de matriz genérico / Costo directo). "Activo físico" permite buscar en el Registro (§6) o crear uno nuevo ahí mismo (con opción de guardarlo al Registro).
- **Por alternativa (A, B, C...):** cada alternativa sigue siendo, como hoy, un `ProjectInput` independiente con su propia `risk_components` — esto es puramente una conveniencia del asistente, no un mecanismo nuevo en `epm_core.py`: al crear una alternativa, el asistente pre-llena su lista de componentes copiando identidad (label, tipo, `source_asset_id`) desde los componentes ya definidos, dejando el valor residual de cada uno en blanco y obligatorio. El motor nunca sabe que dos listas de componentes "están relacionadas" — evalúa cada `ProjectInput` de forma independiente, igual que siempre. Cada alternativa **debe declarar explícitamente** su valor residual para cada componente — sin default automático. Precedente verificado: hoy `residual_consequence_level`/`residual_probability_level` sin declarar valen `1` (mejor caso posible) por defecto en `RiskInput` — un default que infla el valor de una alternativa si el analista olvida un campo. Se decidió no repetir ningún tipo de default silencioso (ver §9).
- **Resultados:** desglose por componente de inacción (`risk_components_results`, §4), junto al desglose por Reto ya existente.

## 8. Manejo de errores y casos de borde

| Caso | Comportamiento |
|---|---|
| `analysis_horizon_years == 0` al evaluar | Error explícito — no hay fallback silencioso a ningún horizonte por defecto |
| `risk_components` vacía | Válido — equivalente al `risk=None` de hoy, el proyecto no tiene componente de riesgo |
| `create()` con `asset_id` ya existente | Error — usar `update()` |
| `update()` con `asset_id` inexistente | Error — usar `create()` |
| `import_csv` con fila de `asset_id` duplicado (contra el Registro o dentro del mismo archivo) | Fila reportada en `errores_por_fila`, no se importa, el resto del archivo continúa |
| Componente tipo "Activo físico" sin `source_asset_id` (creado ad-hoc, sin guardar al Registro) | Válido — el Registro es una conveniencia, no un requisito |
| Alternativa no declara residual para un componente existente | Bloquea el cálculo de esa alternativa — declaración explícita obligatoria (§7) |
| `retire()` sobre un activo referenciado por proyectos ya evaluados | Permitido sin advertencia adicional — no hay enlace vivo que romper (§6.4) |

## 9. Decisiones registradas (para no re-discutirlas en la implementación)

| Decisión | Elegida | Alternativas consideradas / verificación |
|---|---|---|
| Validar contra Copperleaf | Fuentes reales (RFI directo de Copperleaf a EPM, white papers con membrete, sitio público) — **no** documentos propios (`VFDD_EPM_AIP_Rev05.docx` fue descartado explícitamente por el usuario: *"validar con VFDD es darte la razón tú mismo, ese documento lo hiciste tú"* | — |
| Migración de `risk` a `risk_components` | Reemplazo limpio, sin campo paralelo | Mantener ambos en paralelo — descartado: mismo patrón de duplicación que ya causó errores reales en este proyecto (`config.json` vs `DEFAULT_CONFIG`, D-9/D-11/D-12) |
| Alcance de este diseño | Un solo proyecto (registro + componentes múltiples + asistente juntos) | Dos specs secuenciados (registro primero) — el usuario prefirió todo junto |
| Tamaño del Registro de Activos | Núcleo mínimo | Réplica completa de Copperleaf (GIS, adjuntos, comentarios, búsqueda avanzada, millones de activos) — descartado explícitamente por tamaño injustificado para un motor interno |
| Horizonte de análisis | Único por proyecto, declarado, sin sugerencia precargada | Uno por componente de riesgo (propuesta inicial, descartada tras la observación del usuario); horizonte sugerido desde `asset_horizons_by_bu` (descartada, mismo motivo) |
| `delete()` de un activo | `retire()` — soft delete | Borrado real — descartado, rompe trazabilidad de evaluaciones pasadas |
| Colisión de `asset_id` en alta/importación | Error explícito, `create`/`update` separados | Sobrescritura silenciosa |
| Enlace Registro ↔ componente | Copia de valores + `source_asset_id` de trazabilidad | Enlace vivo (referencia que se actualiza sola) — descartado, rompería reproducibilidad de resultados ya calculados |
| Residual no declarado por una alternativa para un componente | Bloquea — declaración explícita obligatoria | Default a "sin cambio" (seguro pero silencioso); híbrido con default + etiqueta visible — el usuario eligió la opción sin ningún default |
| Tipos de inversión afectados | Ya reducidos a 2 en D-13 (independiente de este diseño, ejecutado antes) | — |
