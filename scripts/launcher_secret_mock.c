// Isolated Secret Service fixture for test_player_folder.py. Never installed
// with the player. The account is the exact server/user ID used by the launcher.
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

void* secret_schema_new(const char* name, int flags, ...) {
    (void)name; (void)flags;
    return malloc(1);
}

void secret_schema_unref(void* schema) { free(schema); }

static void secret_path(char* output, size_t size, va_list args) {
    const char* attribute = va_arg(args, const char*);
    const char* account = attribute ? va_arg(args, const char*) : "";
    const char* directory = getenv("TB_TEST_SECRET_DIR");
    if (!directory || !attribute || strcmp(attribute, "account") != 0 || !account || strchr(account, '/')) {
        output[0] = 0;
        return;
    }
    snprintf(output, size, "%s/%s", directory, account);
}

int secret_password_store_sync(const void* schema, const char* collection, const char* label,
                               const char* password, void* cancellable, void** error, ...) {
    (void)schema; (void)collection; (void)label; (void)cancellable; (void)error;
    va_list args;
    va_start(args, error);
    char path[4096];
    secret_path(path, sizeof path, args);
    va_end(args);
    if (!path[0] || getenv("TB_TEST_SECRET_FAIL")) return 0;
    FILE* file = fopen(path, "wb");
    if (!file) return 0;
    fputs(password, file);
    fclose(file);
    return 1;
}

char* secret_password_lookup_sync(const void* schema, void* cancellable, void** error, ...) {
    (void)schema; (void)cancellable; (void)error;
    va_list args;
    va_start(args, error);
    char path[4096];
    secret_path(path, sizeof path, args);
    va_end(args);
    if (!path[0]) return NULL;
    FILE* file = fopen(path, "rb");
    if (!file) return NULL;
    char value[4096];
    const size_t length = fread(value, 1, sizeof value - 1, file);
    fclose(file);
    value[length] = 0;
    return strdup(value);
}

int secret_password_clear_sync(const void* schema, void* cancellable, void** error, ...) {
    (void)schema; (void)cancellable; (void)error;
    va_list args;
    va_start(args, error);
    char path[4096];
    secret_path(path, sizeof path, args);
    va_end(args);
    if (path[0]) unlink(path);
    return 1;
}

void secret_password_free(char* password) { free(password); }
