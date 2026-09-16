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

### C-1 · El CAPEX entra a la ecuación de valor como un escalar — CERRADO para granularidad anual

`CostInput.capex_by_year` existe y alimenta las restricciones de presupuesto del
optimizador, pero **la ecuación de valor no lo usa**. Un proyecto que desembolsa
en tres años se valora igual que uno que desembolsa todo el primer día.

**Choca con el FR23 del documento**, que exige cronogramas de desembolso por mes
y por año. La solución construida debe cerrar esta brecha; el motor de
referencia no la cubre.

**Cerrado en la rama `feature/perfil-capex-por-anio` (pendiente de mergear) —
ver D-9.** `evaluate()` ya valora `capex_by_year` correctamente, y ambos
optimizadores MILP lo consumen y reportan de forma consistente. **Sigue sin
cubrir la granularidad mensual** que también pide el FR23 — `capex_by_year`
solo admite año calendario, no mes. Si el proveedor necesita cronogramas
mensuales, esa es una extensión aparte, no cubierta por este cierre.

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

### D-9 · Perfil de CAPEX por año — cierra C-1, ocho defectos corregidos, dos abiertos

Origen distinto al de D-8: no se encontró verificando contra la documentación
de Copperleaf, sino al diseñar cómo comparar alternativas de horizontes
distintos (pregunta de usuarios en calibración) y descubrir que
`CostInput.capex_by_year` **ya existía**, con una tubería de importación real
desde Excel/PEGA/SAP (`epm_core.py` — el importador de portafolios), pero que
la ecuación de valor lo ignoraba por completo — el asunto C-1 de este mismo
registro. Diseño completo en
`docs/superpowers/plans/2026-08-29-perfil-capex-por-anio.md` y el spec que lo
precede.

**Patrón común a todos los defectos cerrados aquí:** un proyecto con perfil de
CAPEX explícito no se reanclaba cuando el motor probaba un año de arranque
distinto al declarado — se quedaba buscando literalmente los años calendario
originales, y cualquier candidato distinto lo veía como si costara cero o casi
cero. El campo nuevo `ProjectInput.declared_execution_year` (capturado una
sola vez al construir el proyecto, inmune a reasignaciones posteriores de
`execution_year`) es el ancla que permite reanclar.

#### NC-4 · `evaluate()` nunca leía `capex_by_year` — CERRADO

Cualquier proyecto importado con CAPEX repartido en varios años calendario
tenía su VNT calculado como si todo se pagara de una sola vez. Cero cobertura
de pruebas sobre el campo antes de este trabajo.

#### NC-5 · La fórmula de valoración usaba el año de ejecución en vez del rezago compartido — CERRADO

La primera versión de la corrección usaba `delta = execution_year −
declared_execution_year` como desplazamiento temporal. El optimizador de
alcance empresarial reasigna `execution_year` al candidato **y además** pasa
su propio `deferral_years` (relativo al año base del plan, la referencia que
ya comparten los otros tres componentes de la ecuación) — dos referencias
que solo coinciden por casualidad.

| Proyecto declarado en 2029, candidato = año base del plan (`deferral_years=0`) | Valor |
|---|---|
| Nominal (correcto: sin diferir, sin descontar) | 1.000.000.000 |
| Fórmula con `delta` (primera versión) | 1.312.932.375 |
| **Sobrecosto imputado** | **+31,3 %** |

El mismo patrón — y casi el mismo porcentaje — que NC-1 de D-8. Se corrigió a
`offset + deferral_years`, igual que el CAPEX legado.

#### NC-6 · R1 (presupuesto, optimizador de alcance empresarial) no reanclaba y mezclaba ramas — CERRADO

Dos defectos en la misma restricción: (1) el perfil explícito solo "existía"
en los años calendario literales declarados, sin importar qué candidato
probaba el MILP; (2) un año de la ventana de construcción sin tramo declarado
caía al reparto uniforme del CAPEX total, cobrando presupuesto dos veces
cuando el perfil era parcial.

#### NC-7 · La matriz que alimenta el reporte de utilización de presupuesto no reanclaba — CERRADO

No afectaba ninguna decisión del MILP (R1 ya decidía bien de forma
independiente), pero el reporte mostraba 0% de utilización para un proyecto
de perfil explícito en cualquier candidato no-literal. Primera corrección
solo devolvía el tramo del año declarado (subestimaba un perfil de varios
tramos); corregido a la suma completa del perfil.

#### NC-8 · R12 (restricción TOTEX) no reanclaba y solo contaba el primer tramo — CERRADO

A diferencia de NC-7, esta sí es una restricción real del MILP: un proyecto de
dos tramos (4.000 MM + 4.000 MM) contaba solo 4.000 MM contra un techo TOTEX,
colándose bajo un límite que en realidad debía superar.

#### NC-9 · `PortfolioOptimizer` (optimizador simple) sin ninguna prueba, con el mismo anclaje roto — CERRADO

Este optimizador no tenía **ninguna** prueba en la suite antes de este
trabajo. Su matriz de CAPEX anclaba en el índice del `execution_year`
declarado en vez de en cero, afectando también al CAPEX legado (sin perfil).
Y su bucle de escaneo nunca pasaba `deferral_years` — verificado
empíricamente: el VNT del mismo proyecto evaluado en años candidatos
distintos variaba ~0,1-0,2% por año, no un descuento económico real. El
escaneo de año óptimo de este optimizador era económicamente casi inerte,
independiente de todo lo relacionado con `capex_by_year`.

#### NC-10 · `selected["capex_COP"]`/`"vei"` reportaban el escalar sin usar — CERRADO

En **ambos** optimizadores: un proyecto de perfil explícito suele dejar el
campo escalar `capex_COP` en 0, así que el reporte impreso
(`print_portfolio_report`) mostraba CAPEX y VEI en cero para un proyecto que
el motor sí contabilizó y presupuestó bien internamente.

#### NC-11 · Los dos optimizadores usaban referencias distintas para `deferral_years` — CERRADO

Encontrado, a diferencia de NC-4 a NC-10, **no** por la revisión de una tarea
individual sino por la revisión final de todo el trabajo junto — el tipo de
defecto que solo aparece al mirar dos piezas ya cada una aprobada por
separado. `EnterpriseOptimizer` siempre calculó `deferral_years = period.year
− min(años de las vigencias recibidas)`. La corrección de NC-9 en
`PortfolioOptimizer` usaba en cambio el parámetro `base_year` que
`optimize()` recibe, con un comentario que afirmaba —incorrectamente— que así
replicaba lo que ya hacía `EnterpriseOptimizer`. Las dos referencias solo
coinciden cuando `base_year == min(vigencias)`, algo que todo llamador
existente y toda prueba de la suite hacían por convención, no por ninguna
validación que lo exigiera.

| Mismo proyecto, vigencias con `min = 2030`, `base_year = 2020` (desalineado) | VNT |
|---|---|
| `EnterpriseOptimizer` (siempre correcto: usa `min(vigencias)`) | −4.279.401.528 |
| `PortfolioOptimizer` (con el defecto: usaba `base_year`) | −2.612.614.829 |
| **Diferencia** | **~39 %** |

Se corrigió `PortfolioOptimizer` para usar `min(vigencias)`, la misma
referencia real que ya usa `EnterpriseOptimizer` — no `base_year`. Vigilado
por una prueba nueva que fuerza deliberadamente el desalineamiento y confirma
que ambos optimizadores acuerdan.

> **Nota de método.** Cada uno de NC-6 a NC-11 se encontró **durante la
> ejecución** de la tarea que corregía el anterior, o —en el caso de NC-11—
> durante la revisión final que mira todas las tareas juntas. El patrón se
> repitió seis veces: la revisión de calidad independiente (dos etapas, spec
> y calidad, subagente distinto en cada una, más una revisión final de todo
> el trabajo) encontraba una instancia adicional del mismo defecto en otro
> punto del archivo, o una prueba que pasaba por una razón ajena al arreglo
> que decía vigilar, o —NC-11 específicamente— una afirmación de diseño que
> nadie había verificado contra el código del optimizador que decía imitar —
> la misma lección de NC-1 en D-8 (probar la etiqueta, no el comportamiento),
> repetida en NC-7/NC-9/NC-10/NC-11 con exactamente esa forma. En varios de
> esos casos el usuario decidió explícitamente ampliar el alcance de la tarea
> en curso en vez de diferirlo; el registro de decisiones completo queda en
> el historial de commits de la rama.

**Vigilado por el grupo S31**, con campaña de mutación dedicada: 12
mutaciones cubriendo NC-4 a NC-10, más pruebas adicionales para el `capex_COP`
nominal y para NC-11 (halladas en revisiones posteriores a la campaña
formal). Primera corrida de la campaña: 10/12 muertos, 2 sobrevivientes —
ambos por la misma causa de siempre (una prueba medía un campo reportado por
un camino de código distinto al que la mutación realmente rompía). Corregido:
**12/12 mueren.** Suite: 232 → 253 pruebas (+21).

#### Hallazgo abierto · Incentivo a diferir al borde del horizonte

Un proyecto obligatorio de valor neto negativo (que el MILP debe ejecutar
igual) tiene incentivo a diferirse hasta el último período visible de
cualquier horizonte finito, porque ahí un tramo de construcción de varios
años cae fuera de la ventana modelada y no consume presupuesto —
comportamiento **intencional** del diseño (un tramo fuera de ventana no
consume cupo de un plan que no alcanza a verlo), pero explotado por el
objetivo de minimización de costo. **Ya existía antes de este trabajo**,
incluso para CAPEX legado sin perfil explícito — no lo introdujo D-9. Mismo
espíritu que NC-2 de D-8 (el óptimo sigue al parámetro de la ventana, no al
activo). Corregirlo de fondo exige una decisión de diseño: acotar qué
candidatos son válidos según si su ventana de construcción cabe completa en
el horizonte, o documentar el límite explícitamente y dejar que el analista
lo tenga en cuenta.

#### Hallazgo abierto · Dos puntos más con el mismo patrón de NC-7/NC-8/NC-10, sin corregir

Encontrados durante la revisión de calidad de NC-10, deliberadamente dejados
fuera de ese alcance por ser de menor severidad inmediata:

- El bloque de "Gasto Comprometido" (`_capex_committed`) de
  `EnterpriseOptimizer` sigue leyendo el escalar `capex_COP` sin reanclar —
  mismo patrón que NC-7/NC-8/NC-10, en otro punto del archivo.
- `print_portfolio_report()` calcula "presupuesto usado por vigencia" sumando
  `capex_COP` (ya corregido por NC-10 para que sea el total real), pero
  atribuyéndolo **completo** al año de arranque — una aproximación
  preexistente para cualquier proyecto multi-año, con o sin perfil explícito,
  no una regresión de este trabajo.

---

### D-10 · Preselección de alternativas por Valor Anual Equivalente — cierra la brecha 3 del spec de CAPEX/EAV

El motor ya calculaba el Valor Anual Equivalente (`calc_eav()`) precisamente
para evitar que una alternativa de vida larga "ganara injustamente por
acumulación" frente a una de vida corta — así lo dice el propio comentario
del código, escrito antes de esta sesión. Pero el punto donde efectivamente
se decidía cuál alternativa de un `exclusion_group_id` competía por
presupuesto en ambos optimizadores MILP comparaba `net_value_COP` crudo, no
EAV. El EAV se calculaba, se reportaba, pero no decidía nada.

**Corregido:** una función independiente, `_preselect_by_eav(projects, engine)`,
reduce cada grupo de exclusión a una sola alternativa — la de mayor EAV, o
la obligatoria si hay una — antes de que cualquiera de los dos optimizadores
construya sus matrices. Las descartadas no desaparecen: se reportan en
`postponed` con la razón explícita ("Descartada por EAV frente a X"), sin
importar si el solver tiene éxito o falla.

**Nota de método.** El propio proceso de escribir el plan de este trabajo
tenía un defecto: la prueba decisiva original comparaba dos alternativas sin
`asset_type` distinto, así que ambas caían en el mismo horizonte por
defecto — y con horizonte idéntico, EAV es una transformación monótona de
VNT que nunca puede invertir su orden. La premisa del caso era matemáticamente
irrealizable tal como estaba escrita. El subagente implementador lo encontró
en la primera ronda y lo corrigió (activos TyD reales, con vidas útiles
distintas), verificado independientemente por dos revisiones que recalcularon
el escenario a mano contra el motor real. Un segundo hallazgo similar: la
primera versión de la prueba de conexión a `PortfolioOptimizer` pedía que la
descartada por EAV no apareciera en ninguna lista de resultado — contradecía
directamente la sección 6 del spec ("no desaparecen en silencio, se reportan
con la razón"), un error de redacción del propio plan, no del código. Y, como
ya había pasado en D-9 con R1/R12, el primer intento de conectar el reporte
de descartadas en ambos optimizadores lo dejó dentro del bloque que solo se
ejecuta si el solver tiene éxito — un solve infactible las hacía desaparecer
en silencio. Corregido en los dos optimizadores tras la revisión de calidad
de cada uno; la segunda vez (`EnterpriseOptimizer`) se aplicó proactivamente
la lección de la primera, sin que hiciera falta descubrirla de nuevo.

**Vigilado por el grupo S32**, con campaña de mutación dedicada: 6
mutaciones cubriendo la comparación por EAV, la precedencia de lo
obligatorio, la conexión a ambos optimizadores y la visibilidad de
descartadas bajo solve infactible en ambos. Primera y única corrida: **6/6
mueren** — ninguna prueba necesitó fortalecerse. Suite: 253 → 262 pruebas
(+9, partiendo del estado ya cerrado por D-9).

**Hallazgos de la revisión final integral de todo el trabajo (no de ninguna
Task individual) — dos corregidos, uno queda abierto:**

**NC-12 · `_preselect_by_eav` no protegía Gasto Comprometido — CERRADO.**
Solo `is_mandatory` pesaba más que el EAV. Un proyecto con contrato ya
firmado (`is_committed`) podía descartarse por EAV antes de que la
restricción R11 llegara a protegerlo — la misma clase de error que ya se
había corregido para `is_mandatory`, con el agravante de que hay un
contrato de por medio. Corregido: "protegida" ahora es `is_mandatory` **o**
`is_committed`; dos protegidas en el mismo grupo (por cualquier combinación
de las dos banderas) sigue siendo el mismo error de conflicto.

**NC-13 · R3d/Restricción 5 dejaron de ejercitarse en la práctica — CERRADO
(cobertura restaurada, no un cambio de comportamiento).** Como
`_preselect_by_eav` corre sin condiciones antes de que cualquiera de los
dos optimizadores construya sus matrices, cada grupo de exclusión ya llega
reducido a como máximo una alternativa antes de que la restricción propia
del MILP (R3d en `EnterpriseOptimizer`, "Restricción 5" en
`PortfolioOptimizer`) tenga oportunidad de actuar. Verificado
empíricamente: desactivando ambas restricciones por completo, la suite
completa seguía en verde — tres pruebas preexistentes que decían vigilarlas
(`test_r3d_group_exclusion`, la aserción de grupo dentro de
`test_full_integration`, `test_r3d_exclusion_de_grupo_deja_pasar_exactamente_uno`)
habían perdido su poder de detección sin que nada lo notara. No es un
defecto de comportamiento — la salida del motor sigue siendo correcta,
R3d/Restricción 5 ahora son defensa en profundidad genuina, tal como el
spec ya decía que debían quedar. Corregido: una prueba nueva desactiva la
preselección por monkeypatch y confirma que la restricción del MILP, sola,
sigue impidiendo más de una selección por grupo; las tres pruebas
preexistentes quedan anotadas señalando por qué pasan y cuál prueba las
reemplaza como guardián real.

**Hallazgo registrado, no corregido — interacción con Gasto Comprometido y
ETC.** `_preselect_by_eav` sigue sin ninguna noción de Continuous Planning
(`actuals`, `pct_complete`, ETC) más allá de la protección de `is_committed`
que sí se agregó (NC-12) — esa protección cubre el caso "ya hay contrato
firmado", pero no el caso más amplio de un proyecto EN CURSO (`pct_complete`
entre 0 y 1, sin `is_committed`) que compita en un grupo de exclusión: la
preselección puede descartarlo solo por EAV proyectado — no necesariamente
incorrecto (el costo hundido no debería determinar la decisión futura, en
teoría clásica de presupuesto de capital), pero la duda real de fondo es si
`evaluate()` valora esa alternativa por su CAPEX total original o por el
ETC restante que ya calcula `EVMEngine` — si es el total, la comparación no
es de igual a igual. `PortfolioOptimizer` nunca expuso este caso (no tiene
parámetro de `actuals`); este trabajo conecta la preselección a
`EnterpriseOptimizer`, que sí, por primera vez. Resolverlo exige entender la
interacción completa con la lógica de ETC — fuera de alcance de este
trabajo.

---

### D-11 · Reto 6 — Reputación (VD-6)

EPM ajustó el marco de valor y agregó un sexto Reto, Reputación, uniforme
para los 4 negocios, sin definición propia — la metodología de cálculo
sigue siendo la de los referentes (Copperleaf/VDM). Verificado contra la
fuente antes de calibrar nada: **Copperleaf documenta la reputación como
categoría de RIESGO** (DOC-RISK §3.3, *"Assets can carry multiple types of
risk (e.g. reliability, safety, environmental, reputation, etc.)"*), no
como Modelo de Valor positivo — el diseño anterior de EPM (reputación
colapsada en las anclas L1-L5 de la matriz de riesgo, vía VD-1-F, retirado
en `config_v3.0.0`) era, en ese sentido, más fiel a la fuente que el Reto
6 nuevo. El usuario decidió explícitamente seguir adelante de todas
formas, entendiendo que VD-6 es una construcción propia de EPM, no algo
que Copperleaf documente — decisión tomada con la información completa,
no en silencio.

**Diseño:** VD-6-A reactiva la calibración de VD-1-F (`concept_rating`,
curva-S, `scale_max_by_bu` ya calibrado para los 5 alcances) sin inventar
números nuevos. VD-1-F se conserva deprecado (no se borra, conserva
trazabilidad de evaluaciones históricas), con su `deprecated_reason`
actualizado: ya no es sobre la matriz de riesgo, es sobre duplicar VD-6-A.

**El hallazgo que cambió el alcance real del trabajo — verificado, no
supuesto:** la revisión de calidad encontró que `config.json` nunca
definía `risk_matrix.consequence_levels_by_bu` — la tabla que el motor
**realmente** resuelve para TyD/Generación/AyA/Gas (jerarquía documentada:
`consequence_levels_by_bu[BU] > consequence_levels > respaldo`). La
primera corrección de este trabajo solo tocó la tabla plana genérica
(`consequence_levels`), que casi ningún proyecto real usa — para los 4
negocios reales, el motor seguía cayendo en silencio al respaldo embebido
en `DEFAULT_CONFIG` (`epm_core.py`), que todavía incluía el componente
reputacional. La doble contabilización que este trabajo existe para
cerrar seguía viva para el caso real hasta corregir esto.

Esa tabla nunca guardó desglose propio directo/regulatorio/reputacional
(a diferencia de la plana), así que no había un número real que restar.
Se aplicó como **estimación explícita** la misma proporción reputacional
por nivel que ya existe en la tabla plana — marcada en cada entrada como
pendiente de validar con Gestión del Riesgo EPM, no como calibración
firme. Se sincronizó `DEFAULT_CONFIG` (respaldo embebido) con los mismos
números, y se agregó VD-6-A a `INVESTMENT_TYPES`/al ciclo de rezago de
beneficios del asistente interactivo (`ProjectWizard`) — sin eso, VD-6-A
existía en el motor pero era inalcanzable desde la herramienta que el
usuario realmente usa para capturar datos.

**El error propio, encontrado por la propia revisión de calidad — se
documenta con la misma honestidad que los anteriores.** Al reducir el
riesgo mitigado por negocio, dos pruebas preexistentes del grupo S21
(el más sensible del proyecto — existe para atrapar el defecto histórico
de 26-33x donde el riesgo crecía más rápido que el descuento) empezaron a
fallar. Diagnóstico sistemático confirmó que el mecanismo real **no era**
ese defecto histórico (ΔR decrecía con el diferimiento, no crecía) sino
que el CAPEX a valor presente se abarata con el tiempo (inflación de obra
< WACC, propiedad preexistente del motor) más rápido de lo que decrecían
beneficios y el riesgo ya reducido, para 2 de 4 negocios en el escenario
sintético de la prueba. **Primer intento de corrección** (subir la
severidad de riesgo base de la prueba en vez de tocar CAPEX/beneficio):
restauraba las dos aserciones, pero la revisión de calidad reintrodujo
deliberadamente el defecto histórico (sumar de vuelta la exposición
descontada en `_calc_risk`) y encontró que, con esa corrección, **ningún
negocio detectaba el defecto** — la prueba pasaba sin vigilar nada. Se
corrigió reduciendo CAPEX y beneficio en su lugar (severidad de riesgo sin
tocar), verificado con el mismo arnés de reintroducción: el defecto vuelve
a detectarse en 2 negocios. Se agregó una meta-prueba permanente
(`test_grupo_detecta_el_defecto_historico_si_se_reintroduce`) que
reintroduce el defecto y falla si ningún negocio lo atrapa — para que una
futura recalibración de este escenario no pueda repetir el mismo error en
silencio.

**Hallazgo estructural, van dos veces:** esta es la segunda vez en esta
misma sesión que una desincronización entre `config.json` y su respaldo
embebido en Python (`DEFAULT_CONFIG`) produce un defecto real (la primera
fue con `capex_by_year`/R12 en D-9). Se agregó una prueba
(`test_default_config_sincronizado_con_config_json_para_reto_6`) que
compara ambas copias directamente para las estructuras que este trabajo
toca — no resuelve el problema de fondo (dos copias paralelas de la
misma configuración), pero evita que la próxima desincronización pase en
silencio en esta área específica. El diseño de dos copias paralelas queda
como deuda estructural, no resuelta aquí.

**Vigilado por el grupo S25** (las pruebas de VD-6/VD-1-F ya vivían ahí,
no en `S1_ValueEngine` como se planeó originalmente) y por el grupo S21
ampliado con la meta-prueba. Suite: 265 → 271 pruebas (+6).

**Hallazgo registrado, no corregido:** `_preselect_by_eav` (D-10) no tiene
noción de proyectos EN CURSO más allá de `is_committed` — sigue abierto,
sin relación directa con Reto 6, mencionado aquí solo porque ambos tocan
la misma pregunta de fondo (¿cuándo un valor calculado por el motor no
debería competir contra otro?).

---

### D-12 · `consequence_levels_by_bu` no era la suma de su propio desglose por tipo

Encontrado preparando el informe de trazabilidad de riesgo para la sesión
de validación con AyA (2026-09-03) — al documentar "dato a dato" cómo se
llega a cada valor de consecuencia, verificar la tabla agregada
(`consequence_levels_by_bu`, la que el motor resuelve primero) contra su
propio desglose detallado (`consequence_types_matrix`: financiero, H&S,
ambiental, regulatorio, reputacional — por BU y nivel) reveló que dejaron
de coincidir.

**Root cause, verificado antes de tocar nada:** antes de Reto 6,
`consequence_levels_by_bu` **era**, por diseño, la suma exacta de los 5
tipos del desglose — verificado contra los valores "ORIGINAL (con
reputación)" que la nota `_estimado_reputacion` de D-11 dejó registrados:
coinciden en 19 de 20 casos (4 BU × 5 niveles; la única diferencia, en
L1 de AyA y Generación, es de ≤10 MM COP, ruido histórico menor, no el
patrón). Al retirar el componente reputacional en D-11, la aproximación
usada — aplicar la misma *proporción* de reducción que la tabla plana
genérica ("Todos") — rompió esa identidad: la proporción de la tabla
genérica no es la de cada BU real, así que el resultado dejó de ser la
suma de las partes. La brecha crece con la severidad y es sustancial:
hasta **-45 %** en el nivel catastrófico de AyA y TyD (45.000 MM vs.
82.000 MM en AyA; 27.000 MM vs. 49.000 MM en TyD).

**Corrección:** `consequence_levels_by_bu[BU][nivel].value_COP` se
recalculó como la suma EXACTA de `consequence_types_matrix` (financiero +
H&S + ambiental + regulatorio, sin reputacional) para las 4 BU × 5
niveles — 20 valores, en `config.json` y en `DEFAULT_CONFIG`
(`epm_core.py`), sincronizados. La nota `_estimado_reputacion` de cada
entrada se actualizó para documentar el método nuevo y remite a esta
entrada. Se agregó un guardián permanente
(`test_consequence_levels_by_bu_es_la_suma_exacta_del_desglose_por_tipo`,
grupo S25) que falla si la tabla agregada vuelve a desincronizarse de su
propio desglose — confirmado que falla contra los valores viejos antes
del fix, y pasa contra los nuevos. Se actualizaron los valores esperados
de `test_matriz_riesgo_por_negocio_ya_no_cobra_componente_reputacional`
(D-11) para reflejar los montos corregidos. Suite: 271 → 272 (+1).
269/269 pruebas preexistentes siguieron en verde sin recalibrar nada más
— a diferencia de D-11, este cambio (subir el riesgo mitigado) no
reabrió el defecto histórico que vigila S21.

**No es una validación de negocio, es una corrección de consistencia
interna** — el desglose detallado (`consequence_types_matrix`) en sí
mismo sigue siendo el mismo dato sin calibrar formalmente con AyA que
D-11 ya dejó documentado (vive solo en `DEFAULT_CONFIG`, nunca pasó por
`config.json` ni por una sesión de calibración con Gestión del Riesgo
EPM). Este fix solo garantiza que la tabla agregada sea, matemáticamente,
lo que siempre debió ser: la suma de sus propias partes. Si AyA (o
cualquier otro negocio) determina en su validación que alguno de los 5
tipos del desglose está mal calibrado, ese es un ajuste de dato distinto
— el guardián nuevo seguiría exigiendo que la tabla agregada se
recalcule en consecuencia.

---

### D-13 · Se retiran 4 de los 6 Tipos de Inversión — VD-4 reubicado en Crecimiento/Expansión

Decisión del usuario (2026-09-04), durante el diseño del registro de
activos y componentes múltiples de inacción: de los 6 Tipos de Inversión
que existían (`INVESTMENT_TYPES`), se retiran **Cumplimiento
Regulatorio**, **Eficiencia Operativa**, **Sostenibilidad / ESG** y
**Proyecto Empresarial / TI** — quedan solo **Reposición de Activos** y
**Crecimiento / Expansión**. Motivo: no se les vio valor diferencial
frente a los dos que quedan.

**Verificado antes de ejecutar, no solo antes de opinar:**

- **Huérfanos de Retos de valor** — se cruzaron los `mandatory_sc`/
  `suggested_sc` de los 4 tipos retirados contra los 2 que sobreviven.
  Resultado: los 3 sub-criterios del Reto 4 completo (VD-4-A emisiones
  GEI, VD-4-B protección hídrica, VD-4-C multas ambientales) quedaban sin
  ningún tipo de inversión que los sugiriera — Reto 4 entero perdía su
  única puerta de entrada. **Decisión del usuario: reubicar los 3 en
  Crecimiento / Expansión**, como `suggested_sc` (no `mandatory_sc` — no
  todo proyecto de expansión reduce emisiones). VD-2-B (pérdidas de gas) y
  VD-2-C (pérdidas técnicas eléctricas) sí quedan huérfanos — decisión
  explícita del usuario de no reubicarlos.
- **Riesgo real evaluado, no solo mencionado**: existe un portafolio TI
  real y grande (`ti_projects.json`, 426 proyectos; `ti_milp_result.json`,
  385 seleccionados) que usa `"tipo": "Eficiencia Operativa"` como texto
  libre. Verificado que `recalc_ti_risk.py`/`generate_dashboard.py` — el
  pipeline que los procesa — **no importa `epm_core`** y no toca
  `INVESTMENT_TYPES` en ningún punto: es un sistema standalone,
  desacoplado del motor. El riesgo que se había planteado (romper ese
  portafolio) no era real — verificado, no asumido.
- **Bug preexistente encontrado y corregido de paso, sin relación con
  este retiro**: `_INV_TYPE_MAP` (sinónimos de texto libre para
  importación) apuntaba a `"Crecimiento/Expansión"` (sin espacios) cuando
  la clave real en `INVESTMENT_TYPES` siempre fue `"Crecimiento /
  Expansión"` (con espacios) — nunca coincidían. Corregido.
- **Datos muertos encontrados y limpiados**: `config.json` tenía su
  propia sección `investment_types` y `risk_matrix
  .consequence_anchors_by_inv_type`, con nombres de clave que tampoco
  coincidían con `INVESTMENT_TYPES` del motor (`"ESG / Sostenibilidad"`
  vs. `"Sostenibilidad / ESG"`, invertidos). Verificado con `grep` que
  ninguna de las dos secciones se lee en ningún punto del código — eran
  datos huérfanos desde antes de este cambio, no algo que este retiro
  haya roto. Se limpiaron las entradas de los 4 tipos retirados de ambas
  secciones de todas formas, por prolijidad.

**Ejecutado:** `INVESTMENT_TYPES` en `epm_core.py` reducido a 2 entradas;
`_INV_TYPE_MAP` corregido y con los sinónimos de los 4 tipos retirados
eliminados; `config.json` limpiado (secciones muertas + nota de
`_metodologia.portafolio_ti` actualizada); 5 pruebas de
`test_suite_epm_full.py` que usaban los 4 tipos retirados como etiqueta
de escenario (ninguna dependía del tipo para su aserción real — se
verificó que `investment_type` nunca alimenta ningún cálculo, solo es una
etiqueta de reporte) se reasignaron a los 2 tipos que quedan. Verificado
en vivo en el asistente: el desplegable "Tipo de Inversión" muestra
exactamente 2 opciones. Suite: 272/272 en verde, sin cambios de conteo
(mismas pruebas, reasignadas, ninguna eliminada).

---

### D-14 · Componentes múltiples de inacción, Registro de Activos, horizonte único por proyecto

Origen: preguntas de AyA durante el taller de calibración, mientras se
preparaba el informe de trazabilidad de riesgo — *"¿no está la opción
mixta?"* y *"¿no podían existir varios activos degradándose [en un mismo
proyecto]?"*. Verificado contra el código antes de diseñar nada: ninguna
de las dos existía. Un proyecto solo podía declarar un componente de
riesgo (`ProjectInput.risk: Optional[RiskInput]`), y el horizonte de
evaluación salía de una tabla por tipo de activo, no de una decisión
explícita del analista para ese proyecto puntual.

**Motor (`epm_core.py`):**

- `ProjectInput.risk: Optional[RiskInput]` se reemplaza por
  `risk_components: List[RiskInput]` (agregación por suma) — **reemplazo
  limpio, sin campo paralelo de compatibilidad**, decisión explícita
  documentada en el §9 del spec de diseño: mantener los dos campos en
  paralelo se descartó por ser el mismo patrón de duplicación que ya
  produjo errores reales en este proyecto (`config.json` vs.
  `DEFAULT_CONFIG`, ver D-9/D-11/D-12).
- `analysis_horizon_years: int`, declarado por el analista y sin valor
  por defecto, reemplaza la búsqueda de horizonte por tipo de activo en
  toda la ruta central de evaluación: `_evaluate_inner` (beneficios,
  riesgo, EAV, VEI), `evaluate_marginal`, `_calc_timing` (Costo de
  Posponer) y `_optimal_timing_inner` (año óptimo). `get_horizon_for_asset()`
  y `asset_horizons_by_bu` **no se eliminaron** — siguen en uso, ahora
  solo para la Vista de Flota (pestaña de Auditoría), su único consumidor
  restante.
- `RiskInput` gana los campos `label`, `asset_type` y `source_asset_id`
  — identidad y trazabilidad por componente.
- `AssetProfile` gana el campo `status` (`"activo"`/`"retirado"`, borrado
  suave).
- Clase nueva `AssetRegistryStore` (núcleo mínimo del Registro de
  Activos): `create`/`update`/`get`/`list`/`search`/`retire`/
  `import_csv`/`import_excel`/`import_bytes`, sobre el mismo patrón de
  persistencia de `SnapshotStore` (un archivo por registro + `_index.json`).

**Correcciones de paso, encontradas durante la migración** (se dejan
enlazadas aquí para no perderles el rastro):

- Bug preexistente, siempre roto: `SensitivityAnalyst._apply_proj_overrides`
  escalaba `risk_multiplier` contra un campo de `RiskInput`,
  `consequence_level`, que nunca existió. Corregido para escalar
  `baseline_consequence_level` de cada componente.
- Las restricciones R8 (Debe-Mitigar) y R6 (riesgo acumulado máximo) del
  `PortfolioOptimizer` (MILP) se migraron para agregar sobre múltiples
  componentes: R8 con semántica de peor-componente (el máximo baseline
  dispara la restricción; TODOS los componentes deben superar la
  tolerancia del lado residual); R6 suma un proxy tipo EAL a través de
  los componentes.

**Asistente / UI (`app_epm_aip_full_enterprise.py`):**

- El Paso 1 gana un campo obligatorio "Horizonte de análisis" (sin
  sugerencia prellenada) y vuelve "Familia de Activo" condicional a
  `Tipo de Inversión == "Reposición de Activos"`.
- Página/pestaña administrativa nueva "Registro de Activos" (buscar/
  listar con filtros, alta manual, importación masiva CSV/Excel).
- El flujo de "No Hacer Nada" (FASE A) del asistente ahora admite guardar
  MÚLTIPLES "Componentes de Inacción" (uno a la vez, vía "+ Guardar y
  agregar otro componente"), cada uno con la misma captura rica que ya
  existía (modelado Weibull consciente del activo, matriz de consecuencia
  Copperleaf de 5 tipos). Esto también corrigió lo que se había vuelto un
  bug de caída en vivo (`ProjectInput` dejó de aceptar el kwarg `risk=`
  tras la migración del motor).
- La FASE B (definición de alternativa) ahora exige una declaración
  residual EXPLÍCITA para CADA componente guardado, en cada alternativa —
  sin autocompletado ni valor por defecto; la acción "Agregar Alternativa"
  queda bloqueada hasta que todos los residuales estén declarados.
- La visualización de resultados gana una tabla de desglose de ΔR por
  componente (solo se muestra cuando una alternativa tiene 2 o más
  componentes).
- **Limitación conocida y advertida, no corregida aquí** (queda como
  seguimiento aparte): la metodología `asset_aware` (Weibull) todavía no
  agrega correctamente los parámetros Weibull a través de 2+ componentes
  — solo los parámetros físicos (β/η/edad) del ÚLTIMO componente
  ingresado gobiernan el cálculo real de EAL/VNT para esa combinación
  específica. Se agregó un banner de advertencia visible en FASE A y FASE
  B para que un analista que use esa combinación exacta (asset_aware + 2
  o más componentes) no quede engañado en silencio, pero el cálculo de
  fondo sigue necesitando un arreglo propio de Weibull multi-componente.
  **No afecta** a la metodología `driver_based`/`concept_rating`, que ya
  suma riesgo correctamente para cualquier número de componentes, ni a
  las evaluaciones `asset_aware` de un solo componente (el caso
  ampliamente mayoritario), que quedan intactas.

**Referencia:** diseño completo en
`docs/superpowers/specs/2026-09-04-componentes-multiples-inaccion-registro-activos-design.md`
y plan de ejecución en
`docs/superpowers/plans/2026-09-04-componentes-multiples-inaccion-registro-activos.md`.

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
| C-1 CAPEX escalar | Suite, grupo S31 (12 mutantes) | **Cerrado para año** · mes (FR23) sigue abierto |
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
| D-9 NC-4 `evaluate()` no leía `capex_by_year` | Suite, grupo S31 | **Cerrado** |
| D-9 NC-5 Fórmula con `delta` en vez de `deferral_years` | Medido: +31,3 % de sobrecosto | **Cerrado** |
| D-9 NC-6 R1 Enterprise sin reanclar, mezcla de ramas | Suite, grupo S31 | **Cerrado** |
| D-9 NC-7 Reporte de utilización sin reanclar | Suite, grupo S31 | **Cerrado** |
| D-9 NC-8 R12 (TOTEX) sin reanclar | Suite, grupo S31 | **Cerrado** |
| D-9 NC-9 `PortfolioOptimizer` sin pruebas, anclaje roto | Suite, grupo S31 (primera cobertura) | **Cerrado** |
| D-9 NC-10 `selected.capex_COP`/`vei` con escalar sin usar | Suite, grupo S31 | **Cerrado** |
| D-9 NC-11 `deferral_years` con referencia distinta entre optimizadores | Medido: ~39 % de diferencia de VNT | **Cerrado** |
| D-9 Incentivo a diferir al borde del horizonte | Mismo espíritu que NC-2 (D-8) | **Abierto — decisión de diseño** |
| D-9 Gasto Comprometido y reporte por vigencia, mismo patrón | `_capex_committed`, `print_portfolio_report` | **Abierto — menor severidad** |
| D-10 EAV calculado pero sin decidir nada (brecha 3 del spec CAPEX/EAV) | Suite, grupo S32 (6 mutantes) | **Cerrado** |
| D-10 NC-12 `_preselect_by_eav` no protegía Gasto Comprometido | Suite, grupo S32 | **Cerrado** |
| D-10 NC-13 R3d/Restricción 5 sin ejercitarse en la práctica | Suite, grupo S32 (cobertura restaurada) | **Cerrado** |
| D-10 `_preselect_by_eav` sin noción de proyectos EN CURSO/ETC (más allá de `is_committed`) | `_preselect_by_eav`, `EVMEngine` | **Abierto — decisión de diseño** |
| D-11 VD-6 Reputación — brecha real de doble conteo (`consequence_levels_by_bu` ausente) | Suite, grupo S25 | **Cerrado** |
| D-11 Recalibración de S21 que dejó de detectar el defecto histórico (error propio, corregido) | Suite, grupo S21 (meta-prueba nueva) | **Cerrado** |
| D-11 `config.json`/`DEFAULT_CONFIG` desincronizados — segunda vez | Suite, grupo S25 (prueba de sincronía) | **Cerrado para Reto 6 · deuda estructural abierta** |
| D-11 `consequence_levels_by_bu` sin desglose reputacional propio — estimación aplicada | Ver D-12 | **Cerrado por D-12 — corregido, no solo estimado** |
| D-11 VD-6 es una decisión de EPM, no algo que Copperleaf documente | DOC-RISK §3.3 | Documentado, decisión consciente del usuario |
| D-12 `consequence_levels_by_bu` no coincidía con la suma de su propio desglose por tipo (hasta -45% en L5) | Suite, grupo S25 (guardián nuevo) | **Cerrado — recalculado como suma exacta** |
| D-12 `consequence_types_matrix` (el desglose en sí) sigue sin calibrar formalmente con ningún negocio | `epm_core.py → DEFAULT_CONFIG`, nunca en `config.json` | **Abierto — pendiente sesión con Gestión del Riesgo EPM** |
| D-13 Se retiran 4 de 6 Tipos de Inversión (Cumplimiento Regulatorio, Eficiencia Operativa, Sostenibilidad/ESG, Proyecto Empresarial/TI) | `epm_core.py → INVESTMENT_TYPES` | **Cerrado — decisión del usuario, 272/272 pruebas en verde** |
| D-13 VD-4 (Reto 4 completo) reubicado en Crecimiento/Expansión como `suggested_sc` — sin eso quedaba huérfano | `epm_core.py → INVESTMENT_TYPES["Crecimiento / Expansión"]` | **Cerrado** |
| D-13 VD-2-B/VD-2-C quedan huérfanos de todo tipo de inversión — decisión explícita de no reubicarlos | `epm_core.py → INVESTMENT_TYPES` | Documentado, decisión consciente del usuario |
| D-13 `_INV_TYPE_MAP` con sinónimos que nunca coincidían con las claves reales (bug preexistente, sin relación con este retiro) | `epm_core.py → _INV_TYPE_MAP` | **Cerrado** |
| D-14 `ProjectInput.risk` reemplazado por `risk_components: List[RiskInput]` (más `label`/`asset_type`/`source_asset_id` en `RiskInput`, `status` en `AssetProfile`) | `epm_core.py → ProjectInput`, spec §9 | **Cerrado — reemplazo limpio, sin compatibilidad hacia atrás** |
| D-14 Horizonte único por proyecto (`analysis_horizon_years`) reemplaza la búsqueda por tipo de activo en la ruta central de evaluación | `_evaluate_inner`, `evaluate_marginal`, `_calc_timing`, `_optimal_timing_inner` | **Cerrado** · `get_horizon_for_asset()`/`asset_horizons_by_bu` siguen vivos solo para Vista de Flota |
| D-14 `AssetRegistryStore` (núcleo del Registro de Activos) + página administrativa | `epm_core.py → AssetRegistryStore`, pestaña "Registro de Activos" | **Cerrado** |
| D-14 Asistente: horizonte obligatorio en Paso 1, múltiples Componentes de Inacción en FASE A, residual explícito por componente en FASE B, desglose de ΔR por componente | `app_epm_aip_full_enterprise.py` | **Cerrado** |
| D-14 Bug preexistente de `SensitivityAnalyst._apply_proj_overrides` (`risk_multiplier` referenciaba un campo inexistente) y migración de R6/R8 del MILP a múltiples componentes | `SensitivityAnalyst._apply_proj_overrides`, `PortfolioOptimizer` R6/R8 | **Cerrado** |
| D-14 `asset_aware` (Weibull) no agrega parámetros físicos entre 2+ componentes — solo el último ingresado gobierna el cálculo real | Banner de advertencia en FASE A/B del asistente | **Abierto — no afecta `driver_based` ni asset_aware de un solo componente** |

---

### D-15 · Ancla del nivel 5 de Salud y Seguridad (AyA) citaba el VSL bruto, no el neto — no reconciliaba con el valor calibrado

Encontrado al preparar la guía de validación de AyA (2026-09-06): el
criterio de asignación del nivel 5 ("Epidemia regional") describía
"10+ muertes × VSL 4.500M = 45.000M + ..." pero el valor calibrado del
nivel es 40.000M — el propio componente de mortalidad, tal como estaba
descrito, ya superaba el total antes de sumar nada más.

**Root cause, verificado antes de tocar nada:** el nivel 4 del mismo
desglose ("Brote significativo") sí reconcilia — "3 muertes × VSL 2.500M
**neto** = 7.500M + atención masiva = 8.000M" — usando el Valor
Estadístico de la Vida **neto EPM post-ARL** (2.500M), no el bruto DNP
Colombia 2024 (4.500M) que la descripción general del tipo cita como
referencia de mercado. El ancla del nivel 5 usó por error la cifra bruta
(4.500M) en vez de la neta que el propio nivel 4 ya establecía como la
convención vigente para AyA — no fue una discrepancia sobre el monto
calibrado en sí.

**Corrección:** se reescribió el ancla del nivel 5 en `DEFAULT_CONFIG`
(`epm_core.py`, `consequence_types_matrix.hs.levels_by_bu.AyA["5"]`) para
usar la misma convención VSL-neto del nivel 4: "10 muertes × VSL 2.500M
neto = 25.000M + miles de enfermos + demandas masivas + emergencia
estatal + intervención MINSALUD = 40.000M" — reconcilia exactamente con
el `value_COP` ya calibrado (40.000M), que no se modificó. Es una
corrección de texto del criterio, no una recalibración de monto. Ningún
código ni prueba dependía del texto del ancla (`consequence_types_matrix`
no vive en `config.json`, solo en `DEFAULT_CONFIG`, y ninguna prueba fija
su redacción) — verificado con `grep` antes de editar. Suite completa:
307/307 en verde, sin cambios de conteo.

---

### D-16 · Dos tablas Weibull desconectadas — 5 tipos de activo T&D con curva citada eran inalcanzables para el motor de riesgo

Encontrado al cuestionar el usuario, con razón, la certeza que se le dio
sobre la tasa de crecimiento del riesgo de T&D (15%/año, `risk_growth_rate_by_bu`):
esa tasa es el *fallback exponencial* que usa el motor cuando un activo NO
tiene curva de Weibull calibrada — así que antes de aceptar o cuestionar el
15% en sí, había que verificar cuántos activos de T&D realmente dependen de
ese fallback, y por qué.

**Root cause, verificado antes de tocar nada:** existen DOS tablas de
parámetros Weibull en `config.json`, desconectadas entre sí:

- `optimal_timing.weibull_params_by_asset` — la que consulta
  `ValueEngine._get_weibull_params_for_asset()` (motor de riesgo/timing,
  el que alimenta ΔR y el VNT). 5 entradas de T&D, con fuentes dispares
  (`"IEEE C37 — desgaste mecánico"`, sin tabla ni cita puntual; dos
  entradas incluso marcadas `"calibrar con historial EPM"` como
  placeholder).
- `degradation_engine.weibull_beta_by_class` — la que consulta
  `AssetDegradationEngine` (Módulo B, EEOL). 6 entradas de T&D, con
  fuentes citables y verificables: CIGRE/Martin & Tenbohlen (2026,
  *Energies* 19(13):3197, Tabla 3, 26.533 transformadores), IEEE Std
  493-2007 (Gold Book) Tabla 3-13, IEC 62271-200:2021, CIGRÉ TB 379
  (2009), IEC 61511-1:2016, EPRI 3002000829 (2013).

El selector "Tipo de activo" del asistente (`app_epm_aip_full_enterprise.py`,
construcción de `_at_keys`) llena sus opciones desde
`asset_horizons_by_bu[BU]`, que usa la convención de nombres de la SEGUNDA
tabla (`interruptor_potencia`, `celda_MT`, `cable_energia`,
`sistema_control_SCADA`, `medidor_AMI`), no la de la primera
(`interruptor`, `cable_mt`, `cable_at`). Verificado con una llamada directa
al motor: antes de este fix, esos 5 tipos eran seleccionables en el
asistente pero `_get_weibull_params_for_asset()` devolvía `None` para
los 5 — cualquier proyecto de T&D con uno de esos `asset_type` caía en
silencio al exponencial plano (`risk_growth_rate_by_bu.TyD` = 15%/año, sin
fuente verificable — ver cuestionamiento del usuario que originó este
hallazgo) en vez de usar la curva calibrada y citada que ya existía en el
propio `config.json`. Solo `transformador_potencia` funcionaba, por
coincidencia de nombre entre las dos tablas (mismos valores en ambas, sin
conflicto).

**Corrección:** `_get_weibull_params_for_asset()` ahora consulta también
`degradation_engine.weibull_beta_by_class` cuando la clave no está en
`optimal_timing.weibull_params_by_asset` (que conserva prioridad — ninguna
clave existente cambió de valor). Verificado con `git stash`-equivalente
(reimplementación de la función vieja en una llamada aparte) que la
llamada `_get_weibull_params_for_asset("interruptor_potencia")` en efecto
devolvía `None` antes del fix, para confirmar que la prueba nueva
detectaría una regresión real, no una aserción vacía.

Se agregó el grupo **S37** (5 pruebas): los 5 tipos antes inalcanzables
ahora resuelven con el beta/eta exactos de su fuente citada; las 4 claves
que ya funcionaban (`transformador_potencia`, `reconectador`, `cable_mt`,
`cable_at`) no cambiaron de valor; los tipos T&D genuinamente sin curva en
ninguna tabla (`linea_transmision`, `subestacion_civil`) siguen devolviendo
`None`, no una curva inventada; y las entradas de metadato de
`weibull_beta_by_class` (`_nota`, `_revision`, separadores de sección) no
rompen el lookup. Suite completa: 312/312 en verde (307 → 312, +5).

**Lo que sigue abierto:** de los ~25 tipos de activo de T&D en
`asset_horizons_by_bu`, quedan ~14 sin curva Weibull en ninguna tabla
(`linea_transmision`, `subestacion_civil`, `transformador_distribucion`,
`banco_condensadores`, `transformador_medida`, `interruptor_at`,
`interruptor_mt`, `seccionador`, `cable_bt`, `linea_distribucion`,
`rele_proteccion`, `sistema_scada`, `medidor_inteligente`, `reactancia`) —
esos siguen dependiendo del 15%/año exponencial. Y ese 15% en sí sigue sin
una fuente verificable: la nota que lo describe como "calibrado con SIGEAS
EPM + benchmarks IEEE Std 493" no tiene, en todo el repositorio, ningún
dataset, cálculo o documento que la sustente — es una afirmación que se
cita a sí misma. El propio registro ya lo señalaba antes de este hallazgo
(ver **A-2**: un 15% compuesto multiplica el riesgo por 16× en 20 años, y
"es probable que se haya calibrado pensando en un crecimiento aproximado a
corto plazo, no en una tasa sostenida"). Pendiente decidir con el negocio
T&D qué número (o qué mecanismo — quizás no debería ser exponencial sin
cota) es defendible para los ~14 tipos que aún no tienen curva propia.

---

### D-17 · Una cuarta tabla desconectada — `risk_growth_rate_by_asset` usaba nombres que `RiskInput.asset_type` nunca produce

Continuación directa de D-16, investigando la pregunta que D-16 dejó
abierta: ¿de dónde sale realmente `asset_type` en un `RiskInput` de
producción? El asistente (`app_epm_aip_full_enterprise.py`) resultó ser la
única fuente — y no usa ninguna de las dos tablas de D-16.

**Root cause, verificado antes de tocar nada:** existe una TERCERA fuente
de parámetros Weibull, `ASSET_FAMILIES` (`epm_core.py:1818`), indexada por
`asset_family` — un vocabulario más grueso (9 familias en total, 3 para
T&D) que agrupa varios tipos granulares de `asset_horizons_by_bu` bajo un
mismo nombre (`interruptor_seccionador` cubre interruptores Y
seccionadores; `cable_subterraneo` cubre todos los cables MT/AT). Se
verificó con `grep` exhaustivo en los tres archivos de producción
(`epm_core.py`, `app_epm_aip_full_enterprise.py`, `simulaciones_epm_aip.py`)
que `_get_weibull_params_for_asset()` (la función que arregló D-16) **no
se llama en ningún lugar** — las dos únicas referencias son su propio
docstring. Es código correcto y ahora probado, pero muerto.

El emisor real: `w_asset_family` (session_state) se copia directamente a
`RiskInput.asset_type` (`app_epm_aip_full_enterprise.py:5505`), y ese
mismo valor alimenta los `number_input` de β/η en la UI
(`ASSET_FAMILIES[familia]["weibull_defaults"]`) y
`ValueEngine._get_risk_growth_rate()`. Verificado también que
`ProjectWizard` (CLI) nunca asigna `asset_type` a un `RiskInput` — los
proyectos creados por CLI siempre caen al nivel 2 de la jerarquía (tasa
por negocio), nunca al nivel 1.

`risk_growth_rate_by_asset` (el override "más específico", nivel 1 de la
jerarquía de `_get_risk_growth_rate`) tenía 7 claves con la nomenclatura
granular vieja (`interruptor`, `cable_mt`, `bomba_centrifuga`,
`valvula_control`, `scada_sistema`, más `transformador_potencia` y
`tuberia_conduccion` que sí coincidían). Cruzada contra las 9 familias
reales de `ASSET_FAMILIES`, 4 de esas 7 claves nunca eran alcanzables por
ningún `asset_type` real; `scada_sistema` tampoco lo era porque
`ASSET_FAMILIES` no tiene ninguna familia SCADA; y dos familias reales
(`compresor`, `turbina_grupo_hidraulico`) no tenían ninguna entrada.

**Hallazgo adicional, no solo de nombres:** al comparar el β que cada
comentario de `risk_growth_rate_by_asset` dice haber usado para derivar su
tasa contra el β real y citado que hoy tiene `ASSET_FAMILIES` para esa
misma familia, **ninguna de las 6 coincide** — ni siquiera
`transformador_potencia`, la única clave que ya coincidía por nombre
(β asumido≈2,5 al derivar 0,12/año; β real citado hoy, CIGRE 2026: 1,31).
Es decir: aunque se conecten los nombres, los NÚMEROS de esta tabla nunca
fueron recalculados contra la curva que realmente está vigente — es el
mismo patrón de "la etiqueta afirma algo que nadie verificó" de D-15,
aplicado a seis valores en vez de uno.

**Corrección aplicada, con alcance deliberadamente acotado:**
`interruptor`→`interruptor_seccionador`, `cable_mt`→`cable_subterraneo`,
`bomba_centrifuga`→`bomba_impulsion`, `valvula_control`→`valvula_regulador`
— renombradas, mismo valor numérico que antes (NO recalibradas: sería
repetir el error de D-15 dos veces, ahora con prisa). `scada_sistema` se
retiró (clave permanentemente inalcanzable). `compresor` y
`turbina_grupo_hidraulico` se dejaron sin entrada — no se inventó un
número sin base para llenar el hueco; caen correctamente al nivel 2
(tasa por negocio). Cada línea de la tabla quedó con un comentario que
muestra el β asumido originalmente junto al β real citado hoy, para que
la brecha sea visible en el propio código, no solo en este registro.

Se agregó el grupo **S38** (6 pruebas, incluida una que primero verifica
que el caso de prueba elegido realmente distingue el override específico
de la tasa por negocio — la primera versión de esa prueba usaba T&D para
`interruptor_seccionador`, donde ambos niveles dan 0,15 por coincidencia
numérica, y habría pasado igual sin alcanzar el override). Verificado con
una reimplementación de la función vieja en una llamada aparte que las 6
pruebas fallarían contra el código pre-D-17. Suite completa: 318/318 en
verde (312 → 318, +6).

**Lo que sigue abierto:** el problema real — qué tasa (o qué mecanismo)
usar para los ~14 tipos de activo de T&D que no tienen ninguna familia en
`ASSET_FAMILIES` — sigue sin resolver, y ahora se le suma que ni siquiera
los 4 activos con familia y tasa específica tienen esa tasa recalibrada
contra su curva real. Pendiente decidir con el negocio.

---

### D-18 · Duplicados sin deprecar en `asset_horizons_by_bu` + conexión documental Grupo A→familias

Al re-triar los ~16 tipos de activo T&D sin curva Weibull (continuación de
D-16/D-17), aparecieron dos problemas distintos, ambos de la misma familia
de causa raíz (nombres que divergieron entre `DEFAULT_CONFIG` y
`config.json` sin reconciliarse):

**1. Duplicados reales, no solo de nombre.** `sistema_scada`/`medidor_inteligente`
(`DEFAULT_CONFIG`, `epm_core.py`) y `sistema_control_SCADA`/`medidor_AMI`
(`config.json`, la fuente viva) describen el mismo activo físico —
confirmado por el propio comentario de `medidor_inteligente`: `"AMI: vida
regulatoria Res. CREG 038/2014"`. Como `load_config()` mezcla por clave, no
por contenido, las dos versiones sobrevivían juntas en las 4 BU, y el
selector "Tipo de activo" del asistente mostraba ambas como opciones
separadas para el mismo equipo. Verificado con `grep` que config.json
tiene el nombre nuevo en las 4 BU antes de retirar el viejo — sin negocio
que dependiera del nombre legado como única fuente. Se retiraron las 6
entradas legado de `DEFAULT_CONFIG` (TyD, Generación, AyA ×2, Gas).
Ninguna prueba ni código de producción las referenciaba. Suite: 318/318
(sin cambio de conteo, no hacía falta prueba nueva para una eliminación
verificada por ausencia total de referencias).

**2. Cuatro tipos granulares de T&D (interruptor_at, interruptor_mt,
seccionador, cable_bt) son variantes de un equipo que SÍ tiene familia en
`ASSET_FAMILIES`** (`interruptor_seccionador`, `cable_subterraneo`), pero
sin ninguna conexión explícita: un analista que declara uno de estos como
"Tipo de activo" (horizonte) no tiene ninguna señal de que debe elegir esa
familia para la curva Weibull, y el valor por defecto de "Familia de
activo" es `"otro"` (sin curva). Se agregó `granular_asset_types` a cada
familia aplicable, listando los tipos granulares que cubre — incluye
`reconectador`, cuyo β=2,8 en la tabla muerta de D-16 ya coincidía con el
β=2,8 de `interruptor_seccionador`, reforzando que el mapeo no es
arbitrario. **Es solo metadato — no cambia el comportamiento del
asistente**, que sigue exigiendo que el analista elija la familia a mano;
un cambio de UI que sugiera la familia automáticamente queda fuera de
alcance de este hallazgo, sin poder verificarse en vivo dentro de esta
sesión.

Se agregó el grupo **S39** (4 pruebas): cada tipo granular listado existe
de verdad en `asset_horizons_by_bu` de una BU aplicable (no es un nombre
inventado); los 4 tipos del Grupo A están cubiertos; `cable_bt`
específicamente está cubierto; y ningún tipo granular queda asignado a dos
familias a la vez (ambigüedad). Suite completa: 322/322 en verde (318 → 322, +4).

**Grupo C (subestación GIS/AIS/civil) — investigado y cerrado como "no es
un hueco de dato, es un límite de diseño correcto".** Se verificó que
NINGÚN activo agregado/civil de NINGUNA de las 4 BU tiene curva Weibull
propia: ni `presa_concreto`/`presa_tierra`/`compuerta_hidraulica`
(Generación), ni `tanque_concreto`/`tanque_metalico`/`planta_potabilizacion`/
`planta_ptar`/`planta_tratamiento`/`filtro` (AyA) — con la única excepción
de `tanque_almacenamiento` (AyA), que sí tiene curva en
`degradation_engine`. Es un patrón consistente en todo el modelo, no algo
específico de T&D: una subestación (o una presa, o una planta) es un
conjunto de activos con modos de falla distintos, no un componente único.
El mecanismo correcto ya existe — declarar varios componentes de inacción
por proyecto (Paso 2, D-14), uno por activo crítico con familia propia —
en vez de buscar una curva Weibull para el agregado completo. Se documentó
esta decisión de diseño directamente en `config.json`
(`asset_horizons_by_bu.TyD._nota_subestaciones_D18`) para que un futuro
desarrollador no intente "corregir" esto inventando una curva monolítica.
No requirió cambio de código ni prueba nueva — es una aclaración, no un fix
de comportamiento. Suite: 322/322 sin cambio.

**Lo que sigue abierto:** solo los 7 tipos del Grupo D, genuinamente sin
cobertura ni analogía clara: `banco_condensadores`, `linea_transmision`,
`linea_distribucion`, `transformador_distribucion`, `transformador_medida`,
`reactancia`, `rele_proteccion`.

---

### D-19 · Los 7 tipos del Grupo D — investigados con fuentes reales, agregados como familias nuevas en `ASSET_FAMILIES` (no a una tabla muerta)

Continuación directa de D-18. El primer intento de cerrar esto (buscar β/η
en IEEE Std 493 y CIGRE Technical Brochures, las mismas normas ya citadas
en el resto del modelo) chocó con un límite real y verificado: ambos
estándares son de pago, y múltiples intentos de extracción (búsqueda web,
`WebFetch` directo, agregadores secundarios) confirmaron que el contenido
numérico de las tablas no es accesible sin comprarlas. Se documentó esto
explícitamente al usuario en vez de rellenar el hueco con una cifra
aproximada.

**Pivote de fuente.** Se redirigió la búsqueda hacia radicados regulatorios
públicos de distribuidoras eléctricas — el mismo patrón que ya usa
`Estrategia_Reposicion_Transformadores_Generacion_EPM.docx` (negocio
Generación de EPM), que cita un caso real de una cita CIGRE fabricada
("CIGRÉ TB 642 §4 Tabla 4-3: β=3,2/η=32 años") detectada y retirada por
verificación contra el documento primario. Se extrajeron directamente, vía
`pdf.js` en el navegador integrado (los PDF de `aer.gov.au` bloquean
`WebFocus`/`curl` con DNS/CORS pero sí cargan en un tab real), **~20
documentos oficiales**: Asset Management Plans de Energex/Ergon (Energy
Queensland, ante el regulador australiano AER, enero 2024), Technical AMS
de AusNet Services (transmisión y distribución, 2020-2025) y las 233
páginas completas de Ofgem CNAIM v2.1 (metodología obligatoria de todas
las distribuidoras del Reino Unido).

**Hallazgo de arquitectura antes de escribir cualquier número.** Verificar
"primero el fix" (disciplina ya establecida en D-16) reveló que escribir
estos 7 β/η en `degradation_engine.weibull_beta_by_class` o
`risk_growth_rate_by_asset` los habría dejado **exactamente tan inertes
como la tabla original de D-16**: esos 7 tipos no existían como opción en
`ASSET_FAMILIES`, que es la única estructura que realmente alcanza al
analista (`w_asset_family` → `RiskInput.asset_type`, confirmado en D-16/D-17).
El único camino para que el fix sea real es agregar las 7 como familias
nuevas, seleccionables en el desplegable "Familia de activo" para TyD —
confirmado con el usuario antes de tocar código, dado que es un cambio más
grande que calibrar un número (una opción nueva y visible en la app, no
solo datos).

**Los 7 valores, con su nivel de certeza declarado:**

| Familia | β | η (años) | Fuente | Certeza |
|---|---|---|---|---|
| `transformador_medida` | 3,5 | 45 | AusNet AMS 01-09 (29-oct-2020), Tabla 22 "Weibull Rates by Asset" | Alta — citado directo, confirmado por segunda fuente independiente (Energex/Ergon: "expected life... 45 years") |
| `linea_transmision` | 7 | 70 | AusNet AMS 01-09, Tabla 22, fila Conductor | Alta — citado directo, cruzado con Ofgem CNAIM Tabla 20 (conductor Cu 132kV = 70a, coincide exacto) |
| `transformador_distribucion` | 3,5* | 60 | Ofgem CNAIM v2.1 (1-abr-2021), Tabla 20 "Normal Expected Life" | Media — η real (curva propia K/C de CNAIM, no Weibull clásico); β=3,5 por analogía a AusNet fila Transformer (misma clase de activo) |
| `linea_distribucion` | 3,5* | 55 | Energex/Ergon AMP Overhead Conductor (ene-2024) — inicio de riesgo por corrosión SC/GZ | Media — η es un umbral observado, no un η de diseño; β por analogía |
| `reactancia` | 3,5* | 42 | AusNet AMS 10-67 (10-nov-2025) — banda 40-45a "nearing end of life", punto medio | Media-baja — η es punto medio de una banda de reemplazo observada, no un η publicado |
| `banco_condensadores` | 3,5* | 30 ⚠️ | AusNet AMS 10-53 (29-oct-2020) — vida de diseño del reactor serie/inrush | **Sesgo declarado**: el modo de falla dominante real es el "can" (31,8% de notificaciones 2015-2019), que falla antes que el reactor — sin cifra citable del can, η=30 probablemente sobreestima la vida real del banco |
| `rele_proteccion` | 3,5* | 20 | Energex/Ergon AMP Protection Relays (ene-2024) — vida esperada tecnología microprocesador | Media — 3 cifras reales según tecnología (electromecánico 45a/estático 25a/microprocesador 20a); se eligió la más corta porque es la tecnología que EPM adquiere hoy y la de mayor tasa de falla normalizada, no un promedio |

`*` β=3,5 asumido por analogía documentada: es el mismo valor que AusNet
usa para interruptores, transformadores de medida y transformadores de
potencia en su propia Tabla 22 — un patrón real de "equipo eléctrico con
aislamiento papel-aceite/epóxico", no un número inventado sin referencia.
Ningún marco público consultado (AusNet clásico Weibull, Ofgem CNAIM
curva K/C) publica β para estos 5 tipos específicamente — confirmado tras
revisar sus documentos dedicados (AMS 20-58, AMS 20-69, AMS 20-52, AMS
10-68) sin encontrar una tabla de parámetros de forma.

**Corrección propia detectada antes de implementar:** al resumir "los 4
que quedan sin β" se dejó fuera por error a `transformador_distribucion`
(que también necesita β por analogía, ya que CNAIM no publica uno) —
detectado al preparar la implementación, corregido a 5 antes de escribir
código.

**Corrección.** Se agregaron las 7 como entradas nuevas en
`ASSET_FAMILIES` (`epm_core.py`), cada una con `bus_aplicables: ["TyD"]`,
`metodologia_preferred: "asset_aware"`, `weibull_defaults` con la nota de
procedencia y certeza, `modos_falla` y `sc_ids: ["VD-1-A", "VD-1-B"]`
(mismos sub-criterios que `interruptor_seccionador`/`cable_subterraneo`,
ya que su falla afecta la misma consecuencia de continuidad T&D).

Se agregó el grupo **S40** (9 pruebas): los 7 valores citados existen y
coinciden exactamente; **las 7 son alcanzables reproduciendo el filtro
literal que usa la app** para poblar el selector de TyD (la prueba que
D-16 no tenía — no basta con que el dato exista, hay que probar que el
camino de la UI llega a él); las 3 familias previas de TyD siguen
intactas; el total de familias TyD ahora es 11 (3 previas + 7 nuevas +
`"otro"`); ninguna familia nueva pisa una clave existente;
`metodologia_preferred` es `asset_aware` en las 7; las 7 tienen `sc_ids`
no vacíos; `banco_condensadores` declara el sesgo en su nota; y
`rele_proteccion` usa la cifra de microprocesador, no la de electromecánico
(prueba que discrimina la elección real, no solo que exista un número).
Suite completa: 322 → 331 (+9), **331/331 en verde**.

**Lo que sigue abierto:**
- Los 6 rates de `risk_growth_rate_by_asset` (D-17) siguen sin
  recalibrar contra su β real citado hoy.
- El documento de calibración T&D (`Informe_TyD_Guia_Validacion_v1.docx`,
  scratchpad) sigue desactualizado frente a D-16/17/18/19 — no se ha
  tocado por instrucción explícita del usuario ("no quiero el documento").
- `banco_condensadores` con η=30 es una cota optimista conocida — si EPM
  llega a tener datos propios de vida del "can" (SIGEAS o equivalente),
  debe reemplazar esta cifra.
- La sección §2.2 del informe de calibración AyA también quedó
  desactualizada por los hallazgos de D-16 — no se ha planteado al usuario
  aún, se marca aquí para cuando retome trabajo de documento.

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
| S31 | Perfil de CAPEX por año — valoración vía `deferral_years`, reanclaje bajo escaneo, R1/R12/reporte de `EnterpriseOptimizer`, matriz relativa y reporte de `PortfolioOptimizer` (primera cobertura de ese optimizador) | 12 | 12 |
| S32 | Preselección por EAV — gana la alternativa de mayor Valor Anual Equivalente dentro de cada grupo de exclusión, salvo obligatoriedad, en ambos optimizadores, visible aunque el solver falle | 6 | 6 |

La disciplina es la misma en los once grupos: **no basta con probar que el
código no falla, hay que reproducir el valor con un cálculo independiente**.
Un mutante que sobrevive es un camino por el que una implementación
equivocada aprobaría la entrega. Total: 111/111 mutantes probados mueren.
