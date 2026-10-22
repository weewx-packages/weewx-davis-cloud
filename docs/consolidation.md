# Replacing the existing Davis cloud integrations

This document records the acceptance conditions for removing redundant catalog
entries. Installing the new driver alone does not meet these conditions.

## Existing functions to preserve

* DavisConsoleAPI: Console current observations, multiple transmitters, leaf/soil
  stations, AirLink and health; its additional fields, schema and supplied skins
  need an explicit migration path.
* WeatherLink v2 for WeatherLinkIP: current and historical revision-A/B mappings,
  archive catch-up, valid per-record intervals and non-destructive imports.
* Cloud-only WLL and hybrid WLL integrations: cloud weather, health and archive
  access. A hybrid package's local HTTP/UDP functionality remains a separate
  requirement; a cloud-only replacement cannot remove it.
* Standalone health services: optional operation beside another station driver,
  independent polling and compatible persistent observation units.
* Legacy wlink: v1 current conditions and its separate binary historical web
  download. The new v1 adapter currently replaces only current conditions.
  Existing legacy uploaders can instead be evaluated with v2 archive access.

## Acceptance gates

1. Inventory the selected station and its sensors. Save the old configuration,
   schemas and database before testing a candidate configuration in a disposable
   station. API credentials must remain private.
2. Compare weather, rain, soil/leaf, AirLink and health against the existing
   integration at the same observation timestamps. Test missing sensors, zero
   values, bucket sizes, counter resets and stale cloud uploads.
3. Check the existing schema and field units. The installer preserves database
   bindings and never renames columns. Custom DavisConsoleAPI fields and skins
   do not automatically become compatible with normalized observation names.
   A historical millivolt column must not silently start receiving volts.
4. Test hardware catch-up, a request crossing midnight and a real interruption.
   Preserve record intervals and verify that replay introduces no duplicate rows.
5. Check the actual repository client's installation and uninstall transaction,
   source binding, file inventory and rollback behavior.
6. Run the Python 3.10/3.14 and official WeeWX master/development matrix. Include
   installed-engine and targeted mutation checks, plus live device evidence for
   each family declared production-ready.

## Catalog policy

Publish this package as a pre-release until the gates are met. Keep existing
packages, release bytes, history and backups. Once an old package's complete
feature set and migration are verified, remove it from normal discovery through
the catalog's supported retirement mechanism. Never repoint an old package ID
at a different installer or delete its immutable release assets.

Local WLL, Vantage USB/TCP, AirLink HTTP and SDR acquisition remain separate.
No legacy package is retired by this initial implementation.
