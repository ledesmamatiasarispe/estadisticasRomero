# Arquitectura de los 5 módulos Access

Todos los módulos son **frontends sin datos propios** — vinculan tablas de `DatosUnificado2010.accdb`.
Cada `.accdb` en PGR = formularios + queries. Los datos están en el `.accdb` de Datos.

---

## Módulo 1 — Fundiciones-2010PRG

**Propósito:** Carga y control de coladas (cada colada = una sesión de horno).

### Tablas que usa
| Tabla | Rol |
|---|---|
| `Fundiciones` | **CORE** — cabecera de la colada (horas, kg scrap, crisol, campaña) |
| `FundiciónPorFecha` | Fecha de cada colada |
| `ItemProducción` | Piezas producidas en la colada (códpieza, cant moldeada, cant aprobada) |
| `HorasPorFecha` | Horas trabajadas por día |
| `InformesMateriales` | Composición química del metal por material y colada |
| `InformesEspesores` | Dureza por espesor y material |
| `tblInformesControlCalidad` | Informe de control de calidad del lab |
| `Trabajos` | Para saber qué OTs corresponden a la colada |
| `NombreDePiezas`, `PesosDePiezas` | Para mostrar nombre de la pieza |
| `Responsables` | Quién realizó la colada |
| `Impresiones` | Registro de impresiones de informes |

### Queries clave
- `cslInformes` — informe de material completo para imprimir
- `cslInformeEspesores` — dureza por espesor
- `cslInformesPendientes` — coladas sin informe de material
- `cslInformesConPedido y Trabajo` — cruza colada con OT y pedido
- `cslHorasPorFecha` — resumen de horas por período
- `cslPlanCtrlMat` — plan de control de materiales

### Preguntas pendientes
- ¿`FundiciónPorFecha` tiene FK a `Fundiciones`? ¿O se une por fecha?
- ¿`ItemProducción.códpieza` (72% null) se usa o fue reemplazado por `idtrabajo`?

---

## Módulo 2 — Producción-2010PRG

**Propósito:** Planificación y seguimiento de OTs de producción.

### Tablas que usa
| Tabla | Rol |
|---|---|
| `Trabajos` | **CORE** — OTs de producción |
| `ItemDetallePedido` | Item del pedido al que pertenece la OT |
| `Pedidos` | Pedido del cliente |
| `NombreDePiezas`, `PesosDePiezas` | Pieza a producir |
| `Fundiciones`, `FundiciónPorFecha` | Colada asignada a la OT |
| `ItemProducción` | Detalle de producción por colada |
| `ItemDevolución` | Devoluciones vinculadas |
| `SoluciónRechazoInterno` | Causa de los rechazos |
| `RechDevol` | Tabla resumen rechazos+devoluciones |
| `HorasPorFecha` | Horas para cálculos de productividad |
| `EstadisticaMoldeo` | Timestamps inicio/fin de moldeo por OT |
| `ExistenciasEnStock` | Stock disponible |
| `Precios`, `TiposFacturación` | Precios para estimaciones |
| `Responsables` | Moldeador/fundidor asignado |
| `Materiales`, `TiposMaterial` | Material de la pieza |
| `SoluciónDevolución` | Descripción de devolución |

### Queries clave
- `cslProducción` — resumen de producción (aprobado/rechazado/reparado/entregado)
- `cslProgramacionTodas/Nuevas/Viejas` — OTs programadas por estado
- `cslResultadosSemanales Items` — resultados por semana
- `cslResumenAnual Fundiciones / Items` — resumen anual
- `cslDevolPorMes`, `cslDevolPorSemana` — devoluciones por período
- `cslPiezasRechazadasPorColada/Mes` — rechazos por pieza
- `cslSubfrmCargaProduccion` — carga de producción en formulario
- `cslSubfrmItemColada` — detalle de colada
- `cslTrabajosConPedidos` — OTs con su pedido/pieza/cliente
- `cslOrigenResumenFundicionesExcel` — export a Excel

### Lo que confirma
- `RechDevol` es un resumen agregado de rechazos+devoluciones (no tiene datos propios)
- `EstadisticaMoldeo` (8.5k filas) = timestamps por OT → se usa para medir tiempos de ciclo
- `ExistenciasEnStock` está en `OtrosDatosUnificado2010` (tabla separada)

---

## Módulo 3 — Modelos-2010PRG ⭐ (el más completo)

**Propósito:** Gestión de piezas, modelos, clientes, pedidos, remitos, stock, devoluciones y reclamos.
Es el módulo principal de operaciones comerciales.

### Tablas que usa (64 tablas vinculadas)
**Clientes:** `Clientes`, `Domicilios`, `AumentosHistoricos`, `TipoCondicionPago`, `Precios`, `PreciosHistóricos`, `TiposFacturación`

**Piezas y Modelos:** `NombreDePiezas`, `PesosDePiezas`, `Modelos`, `PiezasPorModelo`, `FotosDeModelos`, `MovimientoModelos`, `MovimientosDeStock`, `PartesDeModelo`, `PartesPorModelo`, `TiposModelo`, `PesosDeMoldes`, `RendimientoMetal`, `ReparacionInterna`

**Pedidos y Entrega:** `Pedidos`, `ItemDetallePedido`, `Remitos`, `ItemDetalle`, `RemitosHistoricos`, `Estados`

**Calidad:** `ItemDevolución`, `SoluciónDevolución`, `SoluciónRechazoInterno`, `SoluciónReparacionInterna`, `DocumentoReclamo`, `ItemReclamo`, `DefectosInternos`

**Fichas técnicas:** `InfTécnicaPieza`, `InfTécnicaCalidad`, `InfTécnicaMoldeo`, `InfTécnicaColada`, `InfTécnicaNoyería`, `InfTécnicaRebaba`, `FichasCáscara`, `FichasNoyos`

**Producción:** `Trabajos`, `ItemProducción`, `FundiciónPorFecha`, `EstadisticaMoldeo`, `ModificacionesStock`, `TemporalMovimientoStock`

**Geografía/Soporte:** `Localidades`, `Provincias`, `Proveedores`, `Materiales`, `TiposDomicilio`, `TiposParteDeModelo`, `TiposMaterial`

### Queries clave (136 total — selección)
- `cslClientes` — lista de clientes con condición de pago y datos
- `cslClientesDomicilioLegal/Postal` — domicilios para remitos
- `AAA Devoluciones Pendientes` — devoluciones sin solución
- `AAA Remitos Sin Controlar` — remitos no verificados
- `cslControlDeRemitos` — control de entrega vs pedido
- `csl ERRORES en Reemplazos` — inconsistencias en reemplazos de OT
- `1cslPartesPorModelo` — partes de cada modelo
- `cslPedidosRecibidos` — pedidos ingresados
- `ConsultaPiezasSinMaterial` — piezas sin material asignado

### Lo que confirma
- `ReparacionInterna` (8 filas en `OtrosDatosUnificado`) es un lookup diferente a `SoluciónReparacionInterna`
- `TemporalMovimientoStock` es una tabla de trabajo (vacía en SQLite)
- `RemitosHistoricos` es el historial de remitos anteriores al sistema actual

---

## Módulo 4 — IRC-RAC-2010PRG

**Propósito:** Gestión de documentos de calidad: IRC (Informe de Rechazo de Cliente), RAP (Registro de Acción Preventiva), RAC (Reclamo de Calidad / Acción Correctiva).

### Tablas que usa
| Tabla | Rol |
|---|---|
| `Documentos` | **CORE** — cada documento IRC/RAP/RAC |
| `TiposDocumento` | IRC, RAC, RAP, Mejora |
| `ItemReclamo` | Piezas involucradas en el reclamo |
| `CausasPorReclamo` | Causas formales asignadas |
| `ProblemasPorReclamo` | Problemas identificados |
| `SolucionesPorReclamo` | Acciones tomadas |
| `DocumentoReclamo` | Documentos externos (notas de crédito, etc.) |
| `ReferenciaPedidoReposición` | Pedidos de reposición asociados |
| `ReferenciasNotasDeCrédito` | Notas de crédito emitidas |
| `ItemDevolución` | Vincula con la devolución origen |
| `SoluciónRechazoInterno` | Para cruzar rechazo interno con reclamo |
| `Clientes`, `NombreDePiezas`, `PesosDePiezas` | Datos del cliente y pieza |
| `MejoraContinua` | Registro de mejoras |
| `Resguardo Documentos` | Backup de documentos (en OtrosDatosUnificado) |

### Queries clave
- `cslDocumentosCompleto` — documento completo para visualizar
- `cslImpresionDocumentos` — para imprimir IRC/RAC
- `cslRAC`, `cslRAP` — listados de acciones
- `cslNotasDeCrédito` — notas de crédito emitidas
- `cslPedidosReposición` — pedidos de reposición por reclamo
- `IRC con Devol` — cruza IRC con devolución origen

### Lo que confirma
- `Causas` (33 filas), `Problemas` (28 filas), `Soluciones` (32 filas) son lookups de este módulo
- `{1C55562F...}` son referencias a SharePoint/Outlook adjuntos (no son tablas SQLite)

---

## Módulo 5 — Indicadores.accdb

**Propósito:** Tablero de indicadores y estadísticas (similar a lo que hace la app actual).

### Tablas que usa (24 tablas)
`Clientes`, `DefectosInternos`, `FechasFundición`, `Fundiciones`, `FundiciónPorFecha`, `HorasPorFecha`, `ItemDetalle`, `ItemDetallePedido`, `ItemDevolución`, `ItemProducción`, `Materiales`, `Modelos`, `NombreDePiezas`, `Pedidos`, `PesosDePiezas`, `PiezasPorModelo`, `Remitos`, `RendimientoMetal`, `ReparacionInterna`, `Responsables`, `SoluciónDevolución`, `SoluciónRechazoInterno`, `SoluciónReparacionInterna`, `Trabajos`

### Queries clave (mismas que en DatosUnificado)
- `cslItemTrabajo` — **LA query maestra** que une OT+pieza+pedido+cliente+colada+kg
- `cslPiezas` — piezas con sus pesos
- `cslRendimiento` — rendimiento por colada/material
- `cslResumenAnual Fundiciones / Items` — resumen anual de producción
- `cslRechazoPorMoldeador` — rechazos por operario
- `cslDevolPorSemana` — devoluciones por semana
- `cslHorasPorFecha_Resp_Sector` — horas por sector/responsable
- `cslRptIndicadoresHistoricos` — indicadores históricos
- `Z - DATOS`, `z - DEVOLUCIONES`, `Z - HS TRABAJADAS` — feeds para Excel/gráficos

---

## OtrosDatosUnificado2010.accdb — Datos históricos y auxiliares

Contiene datos que NO están en el SQLite actual (no se sincronizan):

| Tabla | Contenido |
|---|---|
| `AA Rechazos 1998–2004` | Rechazos históricos anteriores al sistema |
| `AA Soluciones 1998–2004` | Soluciones históricas |
| `ExistenciasEnStock` | Stock actual por OT (usado por Producción PRG) |
| `IRC` | IRCs históricos anteriores al sistema de Documentos |
| `MejoraContinua` | Mejoras de proceso |
| `PermisosAccessTablasConsultas` | Control de acceso (usado por Fundiciones PRG) |
| `ReparacionInterna` | Lookup de 8 tipos de reparación (distinto a SoluciónReparacionInterna) |
| `ItemCajón` | Ítem en cajón de stock |
| `Cajones` | Cajones de almacenamiento |
| `SelecciónOrdenes` | OTs seleccionadas (tabla de trabajo) |

---

## Mapa de propietario de cada tabla

| Tabla | Módulo principal | Módulos que la leen |
|---|---|---|
| `Clientes` | Modelos | Producción, IRC-RAC, Indicadores, Fundiciones |
| `NombreDePiezas` | Modelos | Todos |
| `PesosDePiezas` | Modelos | Todos |
| `Pedidos` | Modelos | Producción, Indicadores |
| `ItemDetallePedido` | Modelos | Producción, Fundiciones, Indicadores |
| `Trabajos` | **Producción** | Modelos, Fundiciones, IRC-RAC, Indicadores |
| `ItemDetalle` | Modelos | Producción, Indicadores |
| `Remitos` | Modelos | Indicadores |
| `Fundiciones` | **Fundiciones** | Producción, Indicadores |
| `ItemProducción` | Fundiciones | Producción, Indicadores |
| `HorasPorFecha` | Fundiciones | Producción, Indicadores |
| `InformesMateriales` | Fundiciones | — |
| `ItemDevolución` | Modelos | Producción, IRC-RAC, Indicadores |
| `SoluciónRechazoInterno` | Producción | IRC-RAC, Indicadores |
| `SoluciónReparacionInterna` | Producción | Indicadores |
| `Documentos` | **IRC-RAC** | — |
| `ItemReclamo` | IRC-RAC | Modelos |
| `Modelos` | **Modelos** | Indicadores |
| `EstadisticaMoldeo` | Producción | Modelos |

---

## Conclusión: los 5 módulos del sistema

```
┌─────────────────────────────────────────────────────────┐
│                  DatosUnificado2010                      │
│   (fuente de verdad — todas las tablas viven acá)        │
└──────────────────────┬──────────────────────────────────┘
                       │ vinculan tablas
       ┌───────────────┼──────────────────────┐
       ▼               ▼                      ▼
 ┌──────────┐    ┌───────────┐         ┌───────────┐
 │Fundiciones│   │Producción │         │  Modelos  │
 │  (coladas │   │(OTs/plan) │         │(piezas,   │
 │ informes) │   │           │         │pedidos,   │
 └──────────┘    └───────────┘         │remitos,   │
                                       │stock)     │
                                       └───────────┘
       ┌───────────────┬──────────────────────┘
       ▼               ▼
 ┌──────────┐    ┌───────────┐
 │ IRC-RAC  │    │Indicadores│
 │(reclamos │    │(estadíst.)│
 │calidad)  │    │           │
 └──────────┘    └───────────┘
```
