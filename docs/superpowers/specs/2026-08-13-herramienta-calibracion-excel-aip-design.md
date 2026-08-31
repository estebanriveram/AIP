# Calibrador AIP en Excel — Documento de Diseño

**Creado:** 2026-08-13 · **Última revisión:** 2026-08-18
**Autor:** ASPD — Sostenibilidad, Planeación y Desempeño, EPM
**Estado:** Construido y verificado. Un defecto abierto del modelo (§9.1).

**Entregables**
- `EPM_Calibrador_AIP.xlsm` — el libro (12 hojas)
- `EPM_Calibrador_AIP_VBA.bas` — módulo de macros, instalador de un solo uso

**Cambios aplicados a producción durante este trabajo** (§8)
- `config.json` → v3.7.0 · `epm_core.py` · `app_epm_aip_full_enterprise.py`
- `sim_vs_copperleaf.py` · `simulaciones_epm_aip.py`

> **Nota de versión.** La primera edición de este documento describía una calculadora
> de un solo escenario. La herramienta evolucionó a un asistente de decisión de seis
> pasos que compara cuatro alternativas. Este documento fue reescrito por completo
> para reflejar lo que existe, no lo que se planeó.

---

## 1. Objetivo

Libro Excel autocontenido, sin dependencia de Python, que recrea la fórmula maestra
del Motor Predictivo AIP para:

1. **Decidir** cuál de varias alternativas resuelve mejor una necesidad, y declarar
   si esa elección se sostiene o si los datos no alcanzan para distinguirlas.
2. **Calibrar** los parámetros del modelo viendo su efecto en vivo sobre el VNT.
3. **Enseñar** el AIP: cada cifra se puede recorrer hasta la cantidad física que la
   originó.

La unidad de trabajo es **una necesidad con cuatro alternativas en competencia**, no
un proyecto aislado. Es la misma unidad de `InterventionEngine.evaluate_need()`.

---

## 2. Restricciones

| Restricción | Origen | Consecuencia |
|---|---|---|
| Sin Python | Requisito del usuario | La fórmula se reimplementa en fórmulas de hoja |
| Excel 365 | Confirmado | `LAMBDA`, funciones dinámicas, `TEXTJOIN` disponibles |
| Un solo usuario | Confirmado | `.xlsm` autocontenido, sin empaquetado |
| Sin persistencia entre sesiones | Confirmado | No hay bitácora ni almacén de snapshots |
| El Config de producción no se escribe desde Excel | Decisión D4 | La promoción de una calibración es manual, con aprobación ASPD |

---

## 3. Alcance

### 3.1 Dentro

- Ecuación maestra completa, sus dos ramas de riesgo, VEI y EAV
- Monetización por ruta física y por `concept_rating` (lineal y curva-S)
- Bandas de incertidumbre AACE y veredicto de traslape entre alternativas
- Aplazamiento con sus tres efectos (riesgo, descuento, inflación del CAPEX)
- Marco de valor y marco de riesgo diferenciados por negocio y por tipo de inversión
- Edición estructural: agregar, quitar o modificar retos y sub-criterios

### 3.2 Fuera

- Optimizador MILP de portafolio (`EnterpriseOptimizer`)
- Motor de Degradación completo (`AssetDegradationEngine`, EEOL)
- Motor de Dependencias Temporales
- Trazabilidad ISO 55001 (snapshots SHA-256)
- Escritura de vuelta a `config.json`

---

## 4. Flujo del asistente

El orden no es cosmético: **el AIP mide todo contra la inacción**, así que la línea
base se establece antes que cualquier alternativa. Es el mismo orden que declara el
wizard de producción (`"Duelo: Do Nothing → Alternativas → Resultados"`, FASE A).

| Hoja | Qué se responde |
|---|---|
| `00_Inicio` | Mapa del proceso y la idea central del AIP |
| `P1_Necesidad` | Qué problema y de qué negocio. El negocio define todo el marco de valor |
| `P2_LineaBase` | **Qué pasa si NO hacemos nada.** Cierra cuantificando el costo de la inacción |
| `P3_Alternativas` | Qué caminos existen, cuánto cuestan, qué tan firme es ese costo |
| `P4_Valor` | Cuánto valor genera cada camino. Las preguntas cambian según el negocio |
| `P5_Riesgo` | Cuánto baja el riesgo cada camino respecto a la línea base |
| `P6_Decision` | La recomendación, su nivel de confianza y de dónde viene el valor |
| `M1_Valor` · `M2_Riesgo` · `M3_Bandas` | El motor, visible y editable |
| `C1_Config` · `C2_Plantilla` | Parámetros editables y espejo de solo lectura |

Pestañas por color: verde el asistente, azul el motor, ámbar la configuración.

---

## 5. Decisiones de diseño

### D1 — Reimplementación en Excel, no puente al Motor
Requisito no negociable. Riesgo asumido: divergencia con `epm_core.py` a medida que
el Motor evolucione. Mitigación en §7.

### D2 — Power Query para importar el Config
Parser JSON nativo, sin librerías de terceros. **Refresco manual**, nunca automático.

### D3 — Config en dos capas
`C2_Plantilla` (solo lectura, alimentada por Power Query) separada de `C1_Config`
(copia de trabajo, editable). Sin esa separación, un refresco borraría la calibración
en curso sin aviso.

### D4 — Sandbox: el Config de producción no se escribe desde Excel
Coherente con la política del propio Motor, que exige *"aprobación ASPD documentada"*
para cambiar un peso. Un escritor de JSON en VBA tendría que preservar decenas de
campos `_nota`/`_fuente` sin corromperlos.

### D5 — Modelo residual fiel al Motor
La curva de riesgo tras intervenir es **la misma curva base escalada**, no una curva
nueva con edad reiniciada:

```
ratio_prob      = prob_anual[nivel_residual] ÷ prob_anual[nivel_base]
PoF_residual(k) = PoF_base(k) × ratio_prob
```

Modelar el activo repuesto con curva propia sería más intuitivo pero introduciría un
comportamiento que el Motor no tiene.

### D6 — El cálculo vive en fórmulas de hoja
Una fórmula en una celda es auditable a simple vista; la misma dentro de una función
VBA es opaca. VBA solo orquesta.

### D7 — La rama de riesgo se sugiere por tipo de inversión, pero es editable
El riesgo no siempre es la falla de un activo. Una expansión evita demanda no
atendida; un proyecto de cumplimiento evita una sanción.

| Tipo de inversión | Evento adverso | Rama |
|---|---|---|
| Reposición de Activos | la falla del activo | Weibull |
| Eficiencia Operativa | falla o pérdida de desempeño | Weibull |
| Crecimiento/Expansión | no atender la demanda | Exponencial |
| Cumplimiento Regulatorio | la sanción del ente regulador | Exponencial |
| ESG / Sostenibilidad | el incidente ambiental o social | Exponencial |
| Proyecto Empresarial/TI | indisponibilidad u obsolescencia | Exponencial |

Editable a propósito: hay reposiciones sin datos de deterioro y expansiones que sí
reemplazan un activo viejo.

### D8 — La línea base es un paso propio, no una columna más
Ver §4. Como efecto secundario, el riesgo residual de *No intervenir* es una
**fórmula**, no una casilla: si no se hace nada el riesgo no baja, y eso queda
garantizado por construcción en vez de por disciplina del usuario.

### D9 — `bu_driver_config` es la fuente única de verdad de sub-criterios
Se hallaron **tres taxonomías paralelas** en conflicto (§8.1). La elegida es la que
el analista ve hoy en producción y la más completa.

### D10 — Los sub-criterios de riesgo por activo se excluyen de las preguntas de valor
`VD-1-TR` y `VD-1-TUB` monetizan PoF × CoF, que es exactamente lo que calcula el
Paso 5. Incluirlos en el Paso 4 **duplicaría el ΔR**. Los demás `asset_aware`
—`VD-1-G`/`VD-1-H` de Generación— sí son beneficios y se conservan.

---

## 6. Especificación de cálculo

Todas las fórmulas verificadas contra `epm_core.py`.

### 6.1 Ecuación maestra

```
FD_lag = 1 ÷ (1+WACC)^(años_construcción + offset_beneficios + años_aplazados)

H_ef           = MAX(1, H − años_aplazados)      ← ventana COMÚN entre fechas
Beneficios_NPV = Σ_r (w_r × VD_r) × factor_NPV(H_ef) × FD_lag
CoD            = Beneficios_NPV ÷ (H × 12) × meses_retraso
ΔOPEX_NPV      = ΔOPEX_anual × factor_NPV(H)
ΔR             = riesgo mitigado sobre H_ef − exposición sufrida esperando
CAPEX_nominal  = CAPEX × (1 + inflación)^años_aplazados
CAPEX_VP       = CAPEX_nominal ÷ (1 + WACC)^años_aplazados

VNT = Beneficios_NPV − CoD + ΔR − CAPEX_VP − ΔOPEX_NPV
VEI = VNT ÷ CAPEX_VP       EAV = VNT × WACC ÷ (1 − (1+WACC)^−H)
```

### 6.2 Monetización de sub-criterios

**Ruta física:** `COP_NPV = weight × delta_anual × precio × factor_NPV(H)`

**Ruta `concept_rating`:** normaliza el puntaje contra un neutral en 5
(`score = (calificación − 5) ÷ 5 × 10`) y aplica función lineal o sigmoide
normalizada a los extremos 0 y 10.

Cada sub-criterio se activa con **`Incluir` (1/0)**. Es necesario, no opcional: una
calificación de 0 en un `concept_rating` significa *la peor nota posible*, no *no
aplica*. Sin la bandera, los sub-criterios cualitativos sin usar restaban
−$720.000MM del VNT solo por estar en la hoja. Refuerzo: el valor por defecto de un
`concept_rating` es 5 (neutral).

### 6.3 Riesgo — dos ramas

El Motor cambia de rama según haya o no parámetros Weibull. **Ambas están
implementadas**; la plantilla escoge con el interruptor del Paso 2.

**Rama Weibull** (activo con edad conocida):

```
PoF(edad) = 1 − EXP(−(edad ÷ η)^β)
ΔR_k      = CoF_base × PoF_base_k − CoF_res × PoF_res_k
ΔR_NPV    = Σ_{k=1..H} ΔR_k ÷ (1+WACC)^k  ×  FD_lag_riesgo
```

Se escribe con la forma explícita, **no** con `DISTR.WEIBULL`: elimina el riesgo de
invertir los parámetros (Excel llama *alfa* a la forma y *beta* a la escala, al revés
que el Motor) y es más demostrativa. Blindada contra `η = 0`, que ocurre en la rama
exponencial.

**Rama exponencial** (sin activo que envejece):

```
ΔR_anual   = (CoF_b × PoF_b − CoF_r × PoF_r) × (1+g)^años_aplazados
factor     = (1 − ((1+g)/(1+r))^H) ÷ (r − g)        [anualidad Gordon]
ΔR_NPV     = ΔR_anual × factor × FD_lag_riesgo
```

> **Dos factores de rezago distintos, a propósito.** El riesgo usa
> `años_construcción + aplazamiento`; los beneficios suman además el
> `offset_beneficios`. No es inconsistencia: cada driver puede declarar su propio
> desfase de inicio, pero la reducción de riesgo empieza en la puesta en servicio.

> **Crítico — tabla de consecuencia por negocio.** El Motor resuelve el CoF con
> `consequence_levels_by_bu[BU]`. `config.json` no trae ese bloque, pero la fusión
> con `DEFAULT_CONFIG` lo agrega. Usar la tabla genérica subestima el riesgo: para
> TyD el nivel 4 vale $17.000MM contra $12.000MM. Detectado al comparar contra el
> Motor; no era visible de otra forma.

### 6.4 Aplazamiento

Tiene **seis** efectos, no uno. Los tres primeros favorecen al que espera; los tres
últimos son los que impiden que esperar salga gratis:

1. Escala el riesgo por `(1+g)^d` — el evento que evitarás es mayor
2. Extiende el descuento por rezago — el beneficio llega más tarde
3. Infla el CAPEX un 4% anual — la obra cuesta más
4. **Cobra la exposición**: el ΔR que se deja de capturar en los años que se espera
5. **Acorta la ventana** a `H − d`: aplazar no regala años de beneficio al final.
   Comparar alternativas de fecha exige el mismo horizonte de calendario para todas
6. **Trae el CAPEX a valor presente**: un desembolso futuro no vale lo que uno de hoy

Los tres primeros estaban desde la v1. Sin los tres últimos, el VNT crecía de forma
monótona con el aplazamiento —con `g > r` en los cuatro negocios— y el desvío contra
el Motor llegaba al 30% a tres años y al 232% a ocho.

### 6.5 Bandas AACE y veredicto

```
VNT_bajo = VNT − (CAPEX_alto − CAPEX)     VNT_alto = VNT − (CAPEX_bajo − CAPEX)

Traslape(A,B) = máx(0, mín(alto_A,alto_B) − máx(bajo_A,bajo_B))
                ÷ ancho de la banda MÁS ANGOSTA

Veredicto: traslape(ganadora, 2ª) > umbral → NO RESUELTA;  = 0 → RESUELTA
Empatadas: toda alternativa cuyo traslape con la ganadora > umbral
```

El veredicto se construye sobre bandas de **VNT**, no de CAPEX. Entre alternativas de
una misma necesidad la incertidumbre común se cancela; lo que puede invertir el orden
es que una esté bien estimada y la otra no.

El desempate del ranking es **estable**: entre VNT idénticos gana la columna de la
izquierda, replicando el `sort` estable de Python. Sin eso, `RANK` repite el puesto 1
y `MATCH(2,…)` devuelve `#N/A` — justo en el caso donde el veredicto NO RESUELTA más
importa.

### 6.6 Resolución de WACC y horizonte

```
WACC:      wacc_by_bu[BU] > wacc_by_bu["Todos"] > wacc global
Horizonte: asset_horizons_by_bu[BU]["_default"] > analysis_horizon_years
```

Vigentes: TyD 9,5% · Generación 11,0% · AyA 9,0% · Gas 9,5%.

---

## 7. Fidelidad al Motor

### 7.1 Verificación ejecutada

Toda comparación se hace con Excel calculando de verdad (automatización COM), no con
una simulación aparte.

| Batería | Contraste | Resultado |
|---|---|---|
| 13 escenarios · 4 negocios · ambas ramas | `InterventionEngine.evaluate_need()` | **187/187** |
| Aplazamiento d = 0…7 · 4 negocios | `evaluate(deferral_years=d)` | **8/8** |
| Celdas con error de fórmula | — | **0** |

Los escenarios incluyen casos límite: alternativas idénticas (traslape 100%), opción
única, todas destruyendo valor, y justificación solo por riesgo.

### 7.2 Defectos que la verificación destapó

Ninguno habría sido visible por revisión de escritorio:

| Defecto | Efecto |
|---|---|
| Tabla de consecuencia genérica en vez de por negocio | ΔR subestimado ×1,42 en TyD |
| `concept_rating` en cero leído como peor nota | −$720.000MM espurios |
| Rama exponencial sin implementar | 59% de error en expansiones |
| Aplazamiento sin inflar CAPEX ni extender descuento | hasta 6% de error |
| Aplazamiento sin cobrar exposición, sin acortar ventana ni descontar CAPEX | 30% a 3 años · 232% a 8 años |
| `RANK` sin desempate estable | `#N/A` con alternativas empatadas |
| Fórmula Weibull sin blindar contra `η=0` | 182 celdas `#¡DIV/0!` |

### 7.3 Controles permanentes

1. **Caso de referencia** en `P6_Decision`, con el VNT calculado por el Motor.
2. **Sello de versión** del Config visible en `C2_Plantilla`.
3. Cada módulo referencia la función de `epm_core.py` que replica.

---

## 8. Cambios aplicados a producción

### 8.1 Tres taxonomías en conflicto → una fuente única

Se hallaron tres estructuras paralelas de sub-criterios:

| Estructura | Quién la leía | Editable |
|---|---|---|
| `value_drivers` | `ProjectWizard`, `PortfolioLoader` | Sí |
| `BU_DRIVER_CONFIG` | **la app Streamlit** | **No — hardcodeada** |
| `value_library.reto_metrics` | **nadie** | Sí |

Usaban los mismos IDs para cosas distintas (`VD-4-B` era "Protección hídrica" en una
y "Multas ANLA" en otra), y `investment_types.mandatory_sc` referenciaba IDs
inexistentes en **3 de 4 negocios** para Reposición, Expansión y Cumplimiento.

**Resuelto en v3.7.0:**
- `BU_DRIVER_CONFIG` migrado a `config.bu_driver_config` — ahora editable sin tocar código
- `epm_core.apply_bu_driver_config()` lo aplica **en sitio** (la app hace `from epm_core import BU_DRIVER_CONFIG`, así que reasignar el nombre no bastaría)
- `investment_types` gana `mandatory_sc_by_bu` / `suggested_sc_by_bu` resueltos por negocio
- `risk_matrix.consequence_anchors_by_inv_type` para eventos que no son falla de activo
- `reto_metrics` marcado `_OBSOLETO`

### 8.2 Suites de prueba

**`sim_vs_copperleaf.py`** — 25 FAIL, todos por una causa: la prueba fijaba un WACC
global de 10% y nunca se actualizó cuando el Motor pasó a WACC por negocio (v3.4.0).
El Factor Gordon lo aisló: `exp=35,85` con 10% global, `got=38,74` con 9,5% de TyD.
Se corrigió el **mecanismo** (`w_cfg(bu)`), no los valores esperados. → **41/41**

**`simulaciones_epm_aip.py`** — 15 FAIL por vocabulario desactualizado: el Motor
normaliza el estado del solver a mayúsculas canónicas y la prueba comparaba contra el
`"Optimal"` crudo de PuLP; y un test de degradación usaba el vocabulario de urgencia
del análisis de tiempo óptimo. Además `_cfg()` cargaba el JSON **sin fusionarlo** con
`DEFAULT_CONFIG`, así que la suite ejercitaba una configuración que producción nunca
usa. → **102/103** (§9.1)

### 8.3 Campaña de mutación

Se inyectaron 27 bugs plausibles de uno en uno y se observó si la suite los detectaba.

| | Antes | Después |
|---|---|---|
| Cobertura | 8/27 (30%) | **27/27 (100%)** |
| Riesgo | 0/6 | 6/6 |
| MILP | 4/12 | 12/12 |

Se agregó el grupo **S15** con 19 pruebas, una por brecha. Dos hallazgos del método:

- **Cuatro pruebas nuevas pasaban por la razón equivocada.** Una asertaba una *razón*
  (insensible a un reescalado uniforme); otra atacaba la rama equivocada; dos eran
  vacuas porque el MILP seleccionaba cero. Una prueba que no mata su mutante no es
  una prueba.
- **Dos defectos en el propio arnés**: escritura sin cerrar el archivo (falsos
  supervivientes en Windows) y un clasificador que solo comparaba marcas de tiempo.
  El primero invirtió el resultado de un mutante; el segundo, de los dieciocho.

Advertencia de alcance: 27 mutantes no agotan el espacio de bugs. El 100% dice que la
suite cubre **lo que se supo imaginar**.

---

## 9. Defectos abiertos

### 9.1 Diferir siempre crea valor (g > r) — vigilado por `S15.19`

```
TyD  r=9,5%  g=15,0%      Gas         r=9,5%   g=12,0%
AyA  r=9,0%  g=10,0%      Generación  r=11,0%  g=13,0%
```

El Motor escala ΔR por `(1+g)^aplazamiento` —el riesgo que evitarás es mayor si
esperas— pero **nunca cobra la exposición durante esa espera**. Con `g > r` el VNT
crece de forma monótona con el aplazamiento (2,19B → 4,66B a 12 años, sin techo) y
`optimal_timing_analysis` recomendó **2036**, el extremo de su ventana, con urgencia
FLEXIBLE. Ocurre en **los cuatro negocios**.

`S15.19` queda deliberadamente en rojo: documenta el defecto en vez de esconderlo. Se
pondrá verde cuando ASPD calibre las tasas o el Motor descuente el riesgo asumido
mientras se espera.

### 9.2 Anclajes de consecuencia sin calibrar por ASPD

Los textos de `consequence_anchors_by_inv_type` los redacté por analogía con los de
falla física. Describen el evento correcto pero **no están validados** por el negocio.

### 9.3 Parámetros pendientes de calibración

| Parámetro | Estado |
|---|---|
| Pesos `w_r` | Todos en 1,00 — "ponderación emergente" sin calibrar |
| Tasas de degradación | Solo TyD calibrado (SIGEAS + IEEE 493); AyA/Gas/Generación son benchmarks |
| `approved_by` en el Config | "Pendiente — sesión calibración ASPD" |

La herramienta existe en buena medida para cerrar estos tres.

---

## 10. Rol de VBA

Módulo `mCalibrador`, cinco rutinas, ninguna de cálculo:

| Rutina | Responsabilidad |
|---|---|
| `RevisarCaso` | Coherencia: β/η según la rama, riesgo residual vs base, CAPEX de *No intervenir*, alternativas en competencia. Separa errores de avisos |
| `AplicarRamaSugerida` | Alinea la rama con la del tipo de inversión |
| `RestablecerCaso` | Limpia las cantidades del Paso 4 |
| `VerificarCasoReferencia` | Compara el VNT del libro contra el del Motor |
| `IrAlInicio` / `SiguientePaso` / `PasoAnterior` | Navegación |

Las filas se buscan **por fragmento de etiqueta**, no por número: el módulo sobrevive
a que se agreguen filas. Se verificaron sus 8 hojas, 8 rangos con nombre y 11
búsquedas en los seis tipos de inversión; eso destapó un ancla que resolvía a la fila
del texto de ayuda en vez de a la pregunta.

**Limitación declarada:** el módulo **nunca se ejecutó**. Excel bloquea el acceso
programático al proyecto VBA salvo que se active una opción del Centro de confianza,
que es una configuración de seguridad del equipo y se dejó intacta. Está verificado
que *encuentra* lo que busca, no que su lógica de mensajes sea correcta en ejecución.

El libro **funciona sin las macros**: todo el cálculo vive en fórmulas de hoja.

---

## 11. Riesgos conocidos

| Riesgo | Severidad | Mitigación |
|---|---|---|
| Divergencia entre la plantilla y el Motor | Alta | Caso de referencia, sello de versión, 195 comparaciones automatizadas |
| `g > r` sesga toda recomendación de tiempo óptimo | Alta | `S15.19` en rojo hasta que ASPD decida |
| Pruebas vacuas que pasan sin verificar nada | Media | Campaña de mutación; cotas inferiores en las pruebas de MILP |
| Pérdida de calibración por refresco de Power Query | Media | Arquitectura de dos capas (D3) |
| Calibración que nunca llega al Config real | Media | La transcripción es un paso manual explícito; el libro no persiste historial |

---

## 12. Referencias

- `epm_core.py` — `ValueEngine.evaluate()`, `_calc_risk()`, `_calc_subcriterion()`, `_apply_vf()`, `_pof_weibull_static()`, `InterventionEngine.evaluate_need()`, `estimate_band()`, `overlap_fraction()`, `apply_bu_driver_config()`
- `config.json` v3.7.0 — `bu_driver_config`, `wacc_by_bu`, `asset_horizons_by_bu`, `risk_matrix`, `cost_estimate_uncertainty`, `investment_types`
- AACE International RP 18R-97 — *Cost Estimate Classification System*
- ISO 55001:2024 · Copperleaf C55 — Value Decision Model
- CIGRE / Energies 2026 Tabla 3 — parámetros Weibull de transformadores >200 kV
