// Start the bundled Python the way BeeWare's iOS testbed does (isolated config,
// PYTHONHOME inside the app bundle), then hand over to ios_entry.run().
#include <Python/Python.h>
#include <stdio.h>
#include "PyHost.h"

static int fail(PyConfig *config, PyStatus status, const char *what) {
    fprintf(stderr, "AAS python: %s: %s\n", what, status.err_msg ? status.err_msg : "?");
    PyConfig_Clear(config);
    return 1;
}

int aas_py_run(const char *home, const char *app, const char *packages,
               const char *data, const char *cache, const char *web, const char *key) {
    PyPreConfig preconfig;
    PyConfig config;
    PyStatus status;

    PyPreConfig_InitIsolatedConfig(&preconfig);
    preconfig.utf8_mode = 1;
    status = Py_PreInitialize(&preconfig);
    if (PyStatus_Exception(status)) {
        fprintf(stderr, "AAS python: pre-initialize: %s\n", status.err_msg ? status.err_msg : "?");
        return 1;
    }

    PyConfig_InitIsolatedConfig(&config);
    config.buffered_stdio = 0;
    config.write_bytecode = 0;          // the bundle is read-only
    config.install_signal_handlers = 0; // the app owns signals
    status = PyConfig_SetBytesString(&config, &config.home, home);
    if (PyStatus_Exception(status)) return fail(&config, status, "PYTHONHOME");
    status = PyConfig_Read(&config);
    if (PyStatus_Exception(status)) return fail(&config, status, "read config");
    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) return fail(&config, status, "initialize");
    PyConfig_Clear(&config);

    int rc = 1;
    PyObject *site = PyImport_ImportModule("site");
    PyObject *added = site ? PyObject_CallMethod(site, "addsitedir", "s", packages) : NULL;
    PyObject *sys = PyImport_ImportModule("sys");
    PyObject *path = sys ? PyObject_GetAttrString(sys, "path") : NULL;
    PyObject *app_dir = PyUnicode_FromString(app);
    if (path && app_dir && PyList_Insert(path, 0, app_dir) == 0 && added) {
        PyObject *entry = PyImport_ImportModule("ios_entry");
        PyObject *result = entry ? PyObject_CallMethod(entry, "run", "ssss", data, cache, web, key) : NULL;
        if (result) rc = 0;
        Py_XDECREF(result);
        Py_XDECREF(entry);
    }
    if (PyErr_Occurred()) PyErr_Print();
    Py_XDECREF(app_dir);
    Py_XDECREF(path);
    Py_XDECREF(sys);
    Py_XDECREF(added);
    Py_XDECREF(site);
    return rc;
}
