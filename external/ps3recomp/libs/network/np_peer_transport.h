#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Optional native transport. Set during startup before guest threads run.
 * take_socket transfers a bound datagram socket to sysNet; close notification
 * precedes its host close. filter consumes transport control datagrams. */
typedef struct np_peer_transport {
    int64_t (*take_socket)(void);
    int (*filter)(const void*, uint32_t, const uint8_t[4], uint16_t);
    void (*closed)(void);
} np_peer_transport;
void np_set_peer_transport(const np_peer_transport* transport);
#ifdef __cplusplus
}
#endif
