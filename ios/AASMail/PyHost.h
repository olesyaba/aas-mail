// Embedded Python host for the iPad app (bridging header for Swift).
#ifndef AAS_PYHOST_H
#define AAS_PYHOST_H

/// Initialise the bundled Python 3.12 and run `ios_entry.run(data, cache, web, key)`.
/// Blocks for the lifetime of the server (call it on a background thread).
/// Returns 0 when the server returns normally, 1 on any startup/Python error
/// (the traceback goes to stderr and the server log).
int aas_py_run(const char *home, const char *app, const char *packages,
               const char *data, const char *cache, const char *web, const char *key);

#endif
