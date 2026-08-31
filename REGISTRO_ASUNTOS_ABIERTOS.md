# Registro de Asuntos Abiertos — Motor AIP EPM

**Anexo del documento RN-PEI-0141 · Propuesta de Solución**
Estado a 2026-08-27 · Motor y configuración v3.8.0

---

Este registro acompaña a los archivos entregados. Existe porque **la suite de
pruebas pasa en verde con todos estos asuntos dentro**: ninguno se manifiesta
como un fallo, y sin este documento no habría forma de que el proveedor se
entere de que existen.

Se dividen en tres clases:

| Clase | Quién decide | Efecto si no se cierra |
|---|---|---|
| **A · Calibración** | ASPD con los negocios | El modelo funciona pero con parámetros provisionales |
| **B · Método** | ASPD | El motor calcula distinto a lo que otra área podría esperar |
| **C · Cobertura** | ASPD y proveedor | Hay requisitos del documento que el motor no cubre |

---

## A · Calibración pendiente

### A-1 · Los pesos de los cinco Retos están todos en 1,00

`config.json → value_drivers[].weight`

Los cinco Retos del VFDD pesan exactamente igual en la función de valor. El
propio config lo llama *"ponderación emergente"*: el peso relativo surge de la
monetización de cada sub-criterio, no de una decisión explícita de la Dirección.

Es una postura defendible como punto de partida, pero **no es una calibración**.
Mientras siga así, el modelo afirma que Calidad del Servicio, Cobertura,
Protección Hídrica y Carbono Neutralidad, Servicios Eficientes y Generación de
Valor tienen idéntica importancia estratégica.

Cambiar un peso requiere aprobación ASPD documentada, según nota del propio
config.

### A-2 · La tasa de crecimiento del riesgo supera al costo de capital

`config.json → optimal_timing.risk_growth_rate_by_bu` frente a `wacc_by_bu`

```
TyD          g = 15,0 %    r =  9,5 %
AyA          g = 10,0 %    r =  9,0 %
Gas          g = 12,0 %    r =  9,5 %
Generación   g = 13,0 %    r = 11,0 %
```

**El defecto estructural que esto causaba ya está corregido** (ver §B-0). Lo que
queda abierto es si las tasas son defendibles: un 15 % anual **compuesto**
multiplica el riesgo por 4 en diez años y por 16 en veinte. Es probable que se
haya calibrado pensando en un crecimiento aproximado a corto plazo, no en una
tasa sostenida sobre un horizonte de 30 años.

Vigilado por el grupo **S21** de la suite, que verifica que el invariante se
sostenga *a pesar* de estas tasas.

### A-3 · Los anclajes de consecuencia por tipo de inversión no están validados

`config.json → risk_matrix.consequence_anchors_by_inv_type`

Los textos que describen qué significa cada nivel de consecuencia según el tipo
de inversión fueron redactados por analogía con los anclajes físicos de falla.
**Ningún negocio los ha revisado.** Son los textos que un formulador lee para
elegir un nivel, así que un anclaje mal redactado sesga la valoración de forma
sistemática y silenciosa.

### A-4 · La configuración no está formalmente aprobada

`config.json → _metadata.approved_by = "Pendiente — sesión calibración ASPD"`

Toda la parametrización se entrega con su propio campo de aprobación en blanco.
Debe resolverse antes de que el archivo salga hacia un tercero.

---

## B · Decisiones de método

### B-0 · Tratamiento temporal del diferimiento — CERRADO en v3.8.0

Se documenta porque cambia resultados respecto de cualquier corrida anterior.

Cada término de la ecuación maestra reaccionaba al mismo diferimiento con una
regla distinta. Con `g > r` en los cuatro negocios, el VNT crecía de forma
monótona al postergar y el análisis de año óptimo recomendaba **diferir al borde
de la ventana para cualquier activo**.

Tres correcciones lo cierran:

1. **Se cobra la exposición al riesgo durante la espera.** El ΔR que se deja de
   capturar en los años diferidos se resta. Antes postergar era gratis.
2. **La ventana de evaluación es común.** Diferir *d* años seguía dando los
   mismos *H* años de beneficio, solo que empezando más tarde: cada alternativa
   de fecha se evaluaba sobre una ventana distinta y la que empezaba después
   miraba más lejos en el futuro. Ésta era la causa de fondo.
3. **El CAPEX diferido entra en valor presente**, y el VEI usa ese mismo
   denominador. Antes se inflaba a pesos del año de ejecución y se restaba
   contra beneficios en pesos de hoy: a cinco años de diferimiento se le cobraba
   al proyecto un 57 % más de lo que el desembolso vale hoy.

Vigiladas por las seis pruebas del grupo **S21**. Se verificó que las seis fallan
si las correcciones se retiran. Sin efecto sobre la conformidad C55: 41/41.

### B-0bis · El riesgo Weibull pasó a la tasa de falla anual — CERRADO en v3.8.0

**Requiere ratificación explícita de ASPD porque cambia el orden del portafolio.**

El cálculo del riesgo mitigado tomaba la **probabilidad acumulada**
`F(t) = 1 − exp(−(t/η)^β)` —cuántos de una población instalada el mismo día ya
fallaron— y la multiplicaba por la consecuencia año tras año.

Esa magnitud mide estado de salud, no riesgo anual. Usarla así cobra cada año
por fallas ya ocurridas, y en un activo viejo se satura cerca de 1, lo que
equivale a afirmar que falla con certeza todos los años.

**El propio motor lo tenía advertido**, en el comentario de
`pof_level_thresholds`: *«NO usar annual_prob directamente: F(t) es acumulada,
no tasa anual»*. Existía incluso el mecanismo de traducción, y el cálculo no lo
usaba. Tres módulos aplicaban tres criterios distintos: el motor de valor la
acumulada, la aplicación los umbrales, el motor de degradación la tasa anual.

Ahora el riesgo sale de `h(t) = (β/η)·(t/η)^(β−1)`: la probabilidad de que el
activo falle **este año**, dado que llegó vivo. La acumulada se conserva con su
nombre y sigue siendo el estado de salud.

**Efecto medido**, por clase de activo:

| Clase de activo | Edad | Acumulada | Tasa anual | Razón |
|---|---|---|---|---|
| Transformador de potencia | 35 | 0,0607 | 0,0023 | 25,9× |
| Interruptor de potencia | 25 | 0,4513 | 0,0672 | 6,7× |
| Cable de energía | 30 | 0,4673 | 0,0630 | 7,4× |
| Tubería de hierro dúctil | 50 | 0,4532 | 0,0181 | 25,0× |
| Turbina hidráulica | 28 | 0,4652 | 0,0469 | 9,9× |

**Las reposiciones bajan su riesgo mitigado entre 7 y 26 veces según la clase de
activo, y por tanto pierden peso frente a expansión y cumplimiento cuando
compitan por presupuesto.** Es la corrección de un error, no un cambio de
política, pero reordena el portafolio y ASPD debe ratificarlo antes de que se
use para decidir.

Vigilado por el grupo **S22**, catorce pruebas. Una campaña de mutación sobre
las 69 líneas de esa rama encontró 20 mutantes y **los 20 mueren**. Antes de
escribir el grupo, los 17 primeros sobrevivían todos.

> **Pendiente externo:** la elección entre la tasa anual y la traducción por
> umbrales se hizo con argumento propio —los umbrales dejan la probabilidad
> plana veinticinco años y rompen el análisis de oportunidad—, no con la
> metodología del proveedor. El motor cita un `wp_risk_value §3.1` de Copperleaf
> que zanjaría la pregunta y **no está en la carpeta**. Conviene solicitarlo. Si
> dice otra cosa, esto se revisa.

### B-1 · ¿El OPEX incremental debe arrastrar el rezago de construcción?

Hoy **no**. El OPEX entra como anualidad plana desde el año cero, mientras los
beneficios y el riesgo se descuentan por los años de obra.

Argumento a favor de cambiarlo: la operación empieza cuando el activo entra en
servicio, no el día de la aprobación. Es el mismo criterio que ya se aplica a
todos los demás términos.

Argumento en contra: altera el resultado frente a la referencia de conformidad
C55.

**Magnitud medida:** con dos años de obra, el OPEX incremental de la prueba de
referencia pasa de **$491,7 MM a $410,1 MM**. La conformidad C55 cae de 41/41 a
39/41.

### B-2 · ¿El OPEX incremental debe crecer?

Hoy **no**. Se trata como una anualidad plana.

Sin embargo `config.json → degradation_engine.opex_growth_rate` declara un **8 %
anual**, y el motor de degradación sí lo usa. Es decir: dos partes del mismo
modelo no coinciden sobre si el OPEX crece.

El motor ya tiene la maquinaria de anualidad creciente de Gordon; la usa para
ΔR. Simplemente no la aplica al OPEX.

**Magnitud medida:** aplicar el 8 % declarado con horizonte de 30 años y WACC de
9,5 % multiplica el peso del OPEX incremental por **2,3**. Combinado con B-1, la
cifra de referencia pasa de $491,7 MM a **$942,1 MM** y la conformidad C55 cae a
39/41.

> **B-1 y B-2 se decidieron dejar como están** porque cambiar el tratamiento del
> OPEX modifica la relación del modelo con el estándar contra el que declara
> conformidad. Ajustar el valor esperado de la prueba para que coincida con la
> corrección no sería verificar conformidad, sería sellarla. La pregunta de
> fondo — si el OPEX incremental está expresado en pesos constantes o
> corrientes — es de ASPD.

### B-3 · Existen dos cálculos distintos de `FD_lag`

- El de los **Retos** incluye la rampa propia de cada Reto
  (`benefit_start_offsets`), por eso la ecuación lo escribe `FD_lag(r)`.
- El del **riesgo** usa solo los años de obra, porque el riesgo no se desagrega
  por Reto.

Funciona y es defendible, pero son dos implementaciones del mismo concepto y no
se deduce leyendo la ecuación.

### B-4 · `delta_r_override_COP` deja sin efecto el tratamiento temporal

`ProjectInput.delta_r_override_COP` recibe el ΔR **ya en valor presente**, de
modo que el motor no llama a `_calc_risk()` en absoluto. Eso anula el
escalamiento por diferimiento, el cobro de exposición, la ventana común y el
descuento por rezago: un proyecto evaluado por esa vía responde siempre lo mismo
sin importar cuándo se ejecute.

Se conserva porque los portafolios distintos a Sostenimiento lo usan, y queda
documentado en su propio docstring con la advertencia y la alternativa. Cuando
lo que se conoce es el costo **anual** de no actuar, la vía correcta es
`RiskInput.baseline_annual_COP`, que conserva todo el tratamiento temporal y
está vigilada por el grupo S21.

**Decisión pendiente:** si se migran esos portafolios al modo anual, el campo
puede retirarse.

---

## C · Cobertura incompleta

### C-1 · El CAPEX entra a la ecuación de valor como un escalar

`CostInput.capex_by_year` existe y alimenta las restricciones de presupuesto del
optimizador, pero **la ecuación de valor no lo usa**. Un proyecto que desembolsa
en tres años se valora igual que uno que desembolsa todo el primer día.

**Choca con el FR23 del documento**, que exige cronogramas de desembolso por mes
y por año. La solución construida debe cerrar esta brecha; el motor de
referencia no la cubre.

### C-2 · El motor nunca ha corrido a volumetría de producción

Que resuelva los casos de prueba no dice nada sobre miles de inversiones por
doce períodos. El desempeño es responsabilidad del proveedor y **no puede
inferirse de este motor**, que es un prototipo de calibración sin persistencia
ni capacidad de escala.

### C-3 · Alcance de lo que el motor especifica

Los archivos entregados definen de forma ejecutable la función de valor, el
modelo de riesgo, la degradación de activos, el optimizador y la trazabilidad.

**No cubren**: repositorio de inversiones, etapas de inversión, flujo de
aprobación, control de acceso por rol y división, reportes, tableros, carga y
descarga masiva, e inicio de sesión único. Todo eso se especifica únicamente en
el cuerpo del documento.

---

## D · Verificación de la suite como especificación ejecutable

Estos archivos no solo calculan: son la especificación que un proveedor externo
debe reproducir. Si la suite pasa en verde con una implementación equivocada
por debajo, la suite no está especificando nada — solo aparenta hacerlo. Esta
sección documenta el trabajo de someter la suite a **mutación**: cambiar una
línea del motor de forma que rompa su comportamiento real y comprobar si
alguna prueba lo nota. Ver la nota metodológica al cierre de este documento.

### D-1 · Barrido de mutación sobre el motor completo

Se corrió la misma disciplina que ya había cerrado B-0bis (rama Weibull, 20/20
muertos) sobre los siete dominios del motor: Función de Valor, Costo del
Retraso, MILP núcleo, MILP avanzado, Degradación, Motor de Dependencias, y
Proporcionalidad/métricas.

**Resultado: 82 de 99 mutantes probados sobrevivieron (83 %).** No es un
hallazgo aislado de la rama Weibull — es la condición general de la suite
antes de este trabajo. La causa raíz, repetida en varios dominios, es la misma
que ya se había visto en B-0bis: fixtures de prueba débiles (con VNT negativo
por diseño, o con datos que nunca ejercen de verdad la restricción que dicen
probar) que dejan pasar cualquier implementación, correcta o no.

### D-2 · MILP núcleo (R1-R6) y avanzado (R7-R12) — CERRADO en v3.8.0

**No hubo cambios en `epm_core.py`.** El optimizador ya calculaba correctamente
— lo que faltaba era la vigilancia. El helper `_p()` del grupo S2 construye un
proyecto con VNT fuertemente negativo (un solo sub-criterio modesto contra un
CAPEX grande); el optimizador nunca lo encuentra atractivo, así que
restricciones como la exclusión mutua nunca se ven forzadas a elegir entre dos
proyectos que de verdad compiten.

Se escribieron dos grupos nuevos, **S23** (núcleo) y **S24** (avanzado), con
proyectos de VNT verificado por cálculo directo del motor (no supuesto) que sí
compiten, sí fuerzan las restricciones de piso, mitigación de riesgo,
alineamiento con ventanas de mantenimiento y techo TOTEX, y sí distinguen un
proyecto obligatorio de uno solo atractivo.

Al reconstruir la campaña sobre las 21 líneas señaladas como sobrevivientes
(8 en núcleo, 13 en avanzado, tras la revisión de equivalencia de abajo):
**21/21 mueren.** Suite completa: 179/179 en verde, sin regresión.

Dos mutantes del hallazgo original —invertir la fijación de año de proyectos
en curso (R9) y de gasto comprometido (R11) fuera de su año asignado— se
investigaron aparte y resultaron ser **mutantes equivalentes**: la restricción
R2 de unicidad (`Σ_t x[i][t] ≤ 1`, incondicional para todo proyecto) ya
garantiza matemáticamente lo mismo que esas líneas, así que removerlas no
cambia ningún resultado posible del optimizador. Se verificó de forma empírica
—no solo por lectura del código— mutando una copia y comparando la selección
del optimizador antes y después en dos escenarios representativos: idéntica en
ambos casos. No se les escribió guardián porque no existe entrada que pueda
distinguir la versión mutada de la original.

### D-3 · Función de Valor — Retos (sub-criterio, value function, driver) — CERRADO en v3.8.0

**Tampoco hubo cambios en `epm_core.py`.** Las pruebas del grupo S1 (una por
cada sub-criterio VD-1 a VD-5) solo verifican `VNT > 0` — un chequeo de signo.
No distinguen una implementación correcta de una que aplica el peso del
sub-criterio dos veces, invierte el signo de la escala-S, o ignora la rampa de
un driver: todas seguirían dando VNT positivo.

Se escribió el grupo **S25** — llama directamente a `_calc_subcriterion`,
`_apply_vf` y `_calc_driver` con un cálculo de referencia independiente
(no a través de `evaluate()`), fijando la magnitud exacta de: la multiplicación
delta×precio y el peso del sub-criterio; las constantes C55 (neutral=5,
rango=5) y el divisor de la función lineal; los extremos de la escala-S por
construcción (el aporte en score=±10 debe tocar exactamente ±scale_max, sin
replicar la fórmula sigmoide a mano); el peso del driver (w_r), la rampa
(`benefit_start_offsets`) y el descuento `FD_lag` por rezago; y el bloqueo
exacto del sub-criterio retirado VD-1-F.

Campaña propia de 15 mutaciones sobre estas cuatro funciones: **14/14
aplicables mueren.** Suite completa: 187/187 en verde, sin regresión.

Una mutación (invertir `score >= 0` a `score > 0` en el signo de la
escala-S/lineal) se descartó por ser **equivalente**: en el punto exacto
`score == 0` el resultado es `signo × 0,0` — el mismo valor sin importar qué
signo se use — así que ninguna entrada posible distingue las dos versiones por
el resultado que producen.

### D-4 · Costo del Retraso — CERRADO en v3.8.0

**Tampoco hubo cambios en `epm_core.py`.** No existía ninguna prueba que
fijara la magnitud del CoD (`_calc_timing`) — se ejercía de paso dentro de
evaluaciones con VNT positivo, sin aislar la fórmula ni fijar con qué signo
entra a la ecuación maestra.

Se escribió el grupo **S26**: llama a `_calc_timing` directamente con un
cálculo de referencia independiente para fijar la conversión años↔meses
(`CoD_mensual = beneficios/(horizonte_activo×12)`, `CoD_total = CoD_mensual ×
retraso_años × 12`), que el horizonte usado sea el del activo y no el
horizonte global genérico, que ejecutar ANTES del año óptimo no pague una
bonificación (el retraso se acota a 0, no queda negativo), y una prueba de
extremo a extremo que fija que el CoD se **resta** del VNT (no se suma) y que
se calcula sobre los beneficios ya ajustados por rezago temporal (no los
brutos sin ajustar).

Campaña propia de 6 mutaciones: **6/6 mueren.** Suite completa: 190/190 en
verde, sin regresión.

Dos mutantes candidatos se descartaron por ser inalcanzables con datos reales:
relajar la guarda `delay > 0` a `delay >= 0` no cambia nada porque en
`delay == 0` el resultado ya es `0.0` sin importar la guarda; y la guarda
`horizon_months > 0` nunca se ejercita porque `asset_horizons_by_bu` y su
respaldo en config nunca devuelven un horizonte de 0 o negativo.

### D-5 · Degradación — curvas no-Weibull, EEOL, cruce marginal — CERRADO en v3.8.0

**Tampoco hubo cambios en `epm_core.py`.** `AssetDegradationEngine` es un
motor distinto de `ValueEngine` (alimenta el Módulo B de análisis predictivo)
y sus curvas LINEAR/STEP/ACCELERATED, el piso por condición observada, la
conversión de PoF acumulada a probabilidad anual, las jerarquías de
configuración (consecuencia por negocio, beta/eta por clase de activo) y las
reglas de EEOL y cruce marginal no tenían ninguna prueba que fijara un valor
exacto — se ejercían solo de paso.

Se escribió el grupo **S27**: fija la curva lineal con su tope en 1,0; el
salto exacto de la curva escalonada en el umbral (no después); que el piso
por condición observada de una inspección no quede sepultado bajo un modelo
de edad optimista; el exponente correcto del modelo acelerado; que la
probabilidad de riesgo anual sea la condicional (no la acumulada que ya se
había corregido antes); la jerarquía de consecuencia monetaria por negocio
sobre la genérica, con su respaldo cuando no hay matriz de riesgo; que un
nivel de consecuencia declarado en 1 no se pise con el del config (centinela
es 0, no 3); que el OPEX no reintroduzca el doble conteo de degradación ya
corregido; el año exacto de quiebre del EEOL; el año exacto del cruce
marginal de inversión óptima (con y sin incentivo financiero de esperar); y
que la lectura de beta/eta por clase de activo no confunda sus dos claves de
config (`beta` vs. `_eta_años`).

Campaña propia de 11 mutaciones: **11/11 mueren.** Suite completa: 203/203 en
verde, sin regresión.

### D-6 · Motor de Dependencias — ciclos, exclusión mutua, cascada de CoD — CERRADO en v3.8.0

**Tampoco hubo cambios en `epm_core.py`.** `DependencyEngine` no tenía
ninguna prueba que aislara sus mecanismos — solo se ejercía de paso dentro de
la validación general del optimizador Enterprise, con portafolios que nunca
forzaban un ciclo, una exclusión contradictoria, ni una cadena en el límite
de profundidad.

Se escribió el grupo **S28**: fija que el multigrafo combine
`precedent_project_id` y `dependencies` sin duplicar al padre; que un ciclo
expresado *solo* por `dependencies` se detecte igual que uno expresado por
`precedent_project_id` (los dos mecanismos existen porque el optimizador
Enterprise usa uno y el de scipy el otro); que un ciclo indirecto de tres
nodos se detecte, no solo el caso directo de dos; que una cadena lineal
válida no se reporte como ciclo; que la exclusión mutua contra precedencia se
valide en **ambas** direcciones (hijo excluye a su padre, y padre excluye a
su hijo); el umbral exacto de profundidad de cadena (justo en el límite no
advierte, un eslabón más sí); que la simetría de exclusión mutua se propague
de vuelta cuando se declara en un solo sentido; y el Efecto Cascada de Valor
completo — el lag de precedencia se aplica al calcular cuánto pierde el hijo
por esperar, el peso configurado se aplica a la penalización, el piso en 0
impide que la cascada se vuelva una bonificación si el hijo gana valor al
esperar, se acota correctamente en el borde del horizonte, y el interruptor
de activación/desactivación respeta su sentido.

Campaña propia de 9 mutaciones: **9/9 mueren** (la primera corrida encontró
un hueco real — la deduplicación del multigrafo no tenía guardián — que se
cerró antes de re-confirmar el resultado). Suite completa: 214/214 en verde,
sin regresión.

### D-7 · Proporcionalidad y métricas — EAV, VEI, VEI_EAV, matriz de riesgo — CERRADO en v3.8.0

**Tampoco hubo cambios en `epm_core.py`.** Ninguna de estas fórmulas tenía
una prueba que fijara su valor exacto ni sus fronteras: la prueba de VEI que
ya existía en S1 nunca separaba CAPEX nominal de CAPEX a valor presente (sin
diferimiento, los dos coinciden por construcción, así que un VEI que
dividiera por el nominal habría pasado igual), y ninguna tocaba un umbral
exacto de proporcionalidad ni un límite de tramo de persona-mes.

Se escribió el grupo **S29**: fija la fórmula exacta de EAV y sus dos casos
borde (horizonte ≤0, WACC=0 — un caso donde el resguardo redundante hace que
un desplazamiento en la guarda solo sea observable cuando WACC también es
0); que el VEI y el VEI_EAV usen el CAPEX a valor presente y no el nominal
inflado (usando diferimiento para separarlos, ya que sin él son idénticos);
que el VEI_EAV use el horizonte del activo y no el horizonte global
genérico; el umbral exacto de nivel de rigor analítico (proporcionalidad IAM
SSG) y su jerarquía BU-específica sobre la genérica; la matriz de riesgo 5×5
contra sus valores documentados, con recorte correcto fuera de rango; y los
límites exactos de cada tramo de la tabla de persona-mes.

Campaña propia de 11 mutaciones: **11/11 mueren** (la primera corrida
encontró tres huecos reales — el caso WACC=0 de EAV, y el VEI_EAV usando el
CAPEX nominal — que se cerraron antes de confirmar el resultado final).
Suite completa: 221/221 en verde, sin regresión.

### D-8 · Análisis de año óptimo — dos defectos corregidos, uno abierto

**Esta es la primera entrada de la serie D que cambia `epm_core.py`.** Las seis
anteriores encontraron falta de vigilancia sobre un motor que calculaba bien.
Estas son cuentas equivocadas, encontradas al verificar contra la
documentación de Copperleaf que apareció después de redactar B-0bis.

`optimal_timing_analysis()` es la segunda puerta del motor: produce el año
recomendado que alimenta al optimizador de portafolio. **No la llamaba ninguna
prueba** — las únicas menciones en la suite eran lecturas de configuración.

#### NC-1 · No traía el CAPEX diferido a valor presente — CERRADO

El método inflaba el desembolso por inflación de obra civil y lo restaba
contra beneficios en pesos de hoy, sin descontarlo de vuelta. `evaluate()` sí
lo hacía bien — es la corrección 3 del asunto B-0. Las dos puertas del motor
cobraban distinto por la misma decisión:

| CAPEX de 6.000 MM, diferido 3 años | Valor |
|---|---|
| `evaluate()` — correcto | 5.140.541.987 |
| `optimal_timing_analysis()` | 6.749.184.000 |
| **Sobrecosto imputado** | **+31,3 %** |

Contradice el §4.4 del documento de riesgo de Copperleaf: *"the discount rate
will drive the project cost down into the future"*. El índice de eficiencia
(VEI) heredaba el error en el denominador; también corregido.

#### NC-3 · Cobraba dos veces el rezago de obra en la rama Weibull — CERRADO

`_calc_risk()` ya devuelve el riesgo mitigado multiplicado por su propio
factor de descuento por años de obra, y el análisis lo volvía a multiplicar
por el mismo factor. Efecto medido: **−13,2 % de Valor Neto Total**, igual al
peso a `ΔR × (factor − 1)`. La rama exponencial no tenía el defecto —construye
su valor presente desde cero— y por eso su diferencia era 0,00 exacta.

Vigilado por el grupo **S30**, once pruebas, con recálculo independiente de la
integración Weibull año por año. Campaña de mutación que reintroduce los dos
defectos originales: **8/8 mueren.** Suite: 221 → 232 pruebas, en verde.

> **Nota de método.** La primera versión de S30 dejó vivos 3 de 8 mutantes,
> incluido el defecto original de NC-1. La causa: las pruebas verificaban el
> campo `capex_pv_COP` que el motor *reporta* —y que seguía calculándose
> bien— en vez de comprobar cuál de las dos cifras *entra* a la ecuación.
> Probaban la etiqueta, no el comportamiento. Se corrigieron reconstruyendo el
> componente de costo de forma independiente y añadiendo comprobaciones en
> t>0, donde el descuento por retraso es observable (en t=0 vale 1 y esconde
> el defecto).

#### NC-2 · El año óptimo es siempre una esquina — ABIERTO

**No se corrigió, y no era consecuencia de NC-1**: persiste con NC-1 y NC-3 ya
cerrados. El diagnóstico correcto no es "el óptimo siempre es hoy" sino que
**el óptimo es siempre un extremo de la ventana de escaneo**, y cuál de los
dos depende de si domina el riesgo o el CAPEX:

| Escenario | Año óptimo |
|---|---|
| Riesgo domina (activo viejo, CAPEX moderado) | siempre 0 |
| CAPEX domina (activo joven, CAPEX grande) | siempre el borde del horizonte |

Y en el segundo caso **el resultado sigue al parámetro, no al activo**:
con `horizon_years` de 3, 5, 8, 12, 20 y 30, el óptimo devuelto fue
exactamente 3, 5, 8, 12, 20 y 30.

Esto es, palabra por palabra, el defecto que el propio código dice haber
corregido — pero en el otro motor. El docstring de
`AssetDegradationEngine._optimal_year_by_marginal_crossing` lo describe:
*"el mínimo cae siempre en el último año escaneado, cambiando con
horizon_years sin que cambie el activo"*, y lo resuelve con una regla de cruce
marginal *"insensible a horizon_years"*. **Esa corrección nunca se llevó a
`ValueEngine.optimal_timing_analysis`.** El mismo defecto vive en dos lugares;
se arregló en uno.

Consecuencia práctica: el análisis de año óptimo del motor de valor no aporta
información — su respuesta está determinada por la ventana. Y el motor no
puede reproducir el ejemplo del §4.2 de Copperleaf, donde el óptimo es
interior (2031), distinto tanto del año de máxima mitigación de riesgo (2028)
como del de menor costo total (2035).

**Decisión pendiente:** llevar la regla de cruce marginal ya implementada en
el motor de degradación a `optimal_timing_analysis()`, o declarar que el año
óptimo del motor de valor es informativo y que el año que gobierna es el del
motor de degradación. No es una corrección aritmética: cambia la semántica de
un método cuyo resultado alimenta al optimizador (`recommended_start_year`).

---

**Con este cierre terminan los siete dominios del barrido de mutación D-1**
(MILP núcleo/avanzado, Función de Valor, Costo del Retraso, Degradación,
Motor de Dependencias, y Proporcionalidad y métricas). De los 99 mutantes
originalmente probados, 82 sobrevivían (83%); ahora los 82 mueren, sin que
se haya modificado una sola línea de `epm_core.py` — todo el trabajo fue
cerrar la vigilancia que faltaba en `test_suite_epm_full.py`. Suite: de 164
a 221 pruebas (+57).

---

## Trazabilidad

| Asunto | Verificable en | Estado |
|---|---|---|
| A-1 Pesos en 1,00 | `config.json → value_drivers[].weight` | Abierto |
| A-2 Tasas g ≥ r | Suite, grupo S21 | Estructura cerrada · tasas abiertas |
| A-3 Anclajes sin validar | `risk_matrix.consequence_anchors_by_inv_type` | Abierto |
| A-4 Sin aprobación formal | `_metadata.approved_by` | Abierto |
| B-0 Tratamiento temporal | Suite, grupo S21 (6 pruebas) | **Cerrado v3.8.0** |
| B-0bis Riesgo Weibull anual | Suite, grupo S22 (14 pruebas) | **Cerrado v3.8.0 · falta ratificar** |
| B-1 Rezago del OPEX | Medido: 491,7 → 410,1 MM | Abierto |
| B-2 Crecimiento del OPEX | Medido: 491,7 → 942,1 MM | Abierto |
| B-3 Dos `FD_lag` | `_calc_driver` y `_calc_risk` | Documentado |
| B-4 Override sin tratamiento temporal | `ProjectInput.delta_r_override_COP` | Documentado · migración abierta |
| C-1 CAPEX escalar | Choca con FR23 | Abierto |
| C-2 Escala no probada | — | Responsabilidad del proveedor |
| C-3 Alcance del motor | — | Documentado |
| D-1 Barrido de mutación (7 dominios) | 82/99 sobrevivientes iniciales | Diagnóstico completo |
| D-2 MILP núcleo + avanzado (R1-R12) | Suite, grupos S23+S24 (21 mutantes) | **Cerrado v3.8.0** |
| D-3 Función de Valor — Retos | Suite, grupo S25 (14 mutantes) | **Cerrado v3.8.0** |
| D-4 Costo del Retraso | Suite, grupo S26 (6 mutantes) | **Cerrado v3.8.0** |
| D-5 Degradación — curvas no-Weibull | Suite, grupo S27 (11 mutantes) | **Cerrado v3.8.0** |
| D-6 Motor de Dependencias | Suite, grupo S28 (9 mutantes) | **Cerrado v3.8.0** |
| D-7 Proporcionalidad y métricas | Suite, grupo S29 (11 mutantes) | **Cerrado v3.8.0** |
| D-8 NC-1 CAPEX diferido sin descontar | Suite, grupo S30 (8 mutantes) | **Cerrado — motor corregido** |
| D-8 NC-3 Doble rezago de obra (Weibull) | Suite, grupo S30 | **Cerrado — motor corregido** |
| D-8 NC-2 Año óptimo de esquina | Medido: el óptimo sigue a `horizon_years` | **Abierto — decisión de diseño** |

---

## Cobertura de la suite

| Grupo | Qué vigila | Mutantes probados | Muertos |
|---|---|---|---|
| S21 | Tratamiento temporal del diferimiento | 6 | 6 |
| S22 | Rama Weibull: riesgo de un activo que envejece | 20 | 20 |
| S23 | MILP núcleo — presupuesto, unicidad, dependencias, exclusión, persona-mes (R1-R6) | 8 | 8 |
| S24 | MILP avanzado — piso de inversión, mitigación de riesgo, completados, ventanas de mantenimiento, TOTEX (R7-R12) | 13 | 13 |
| S25 | Función de Valor — sub-criterio, value function, agregación de driver | 14 | 14 |
| S26 | Costo del Retraso — conversión años/meses, horizonte del activo, signo en el VNT | 6 | 6 |
| S27 | Degradación — curvas no-Weibull, piso de condición, EEOL, cruce marginal | 11 | 11 |
| S28 | Motor de Dependencias — ciclos, exclusión mutua, cascada de CoD | 9 | 9 |
| S29 | Proporcionalidad y métricas — EAV, VEI, VEI_EAV, matriz de riesgo, persona-mes | 11 | 11 |
| S30 | Año óptimo — CAPEX a valor presente, rezago de obra una sola vez, coherencia entre las dos puertas | 8 | 8 |

La disciplina es la misma en los nueve grupos: **no basta con probar que el
código no falla, hay que reproducir el valor con un cálculo independiente**.
Un mutante que sobrevive es un camino por el que una implementación
equivocada aprobaría la entrega. Total: 99/99 mutantes probados mueren.
