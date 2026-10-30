#define PY_SSIZE_T_CLEAN
#include <Python.h>

static long long spin(long long n) {
    volatile long long total = 0;
    for (long long i = 0; i < n; i++) {
        total += i * i % 7;
    }
    return total;
}

static PyObject *fastsum_spin(PyObject *self, PyObject *args) {
    long long n;
    if (!PyArg_ParseTuple(args, "L", &n)) {
        return NULL;
    }
    return PyLong_FromLongLong(spin(n));
}

static PyMethodDef methods[] = {
    {"spin", fastsum_spin, METH_VARARGS, "Spin n times."},
    {NULL, NULL, 0, NULL}};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "fastsum", NULL, -1, methods};

PyMODINIT_FUNC PyInit_fastsum(void) { return PyModule_Create(&module); }
