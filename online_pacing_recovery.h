#pragma once
#include <cstdint>

namespace tb_pacing_recovery {
inline bool newer(uint32_t a,uint32_t b) {
    const uint32_t distance=a-b;
    return distance && distance<0x80000000u;
}

// One optional render, never a simulation or clock change. Ownership and
// ordering remain conservative until a lifecycle reset establishes a new epoch.
struct RenderState {
    static constexpr uint64_t freshness_ns=50000000;
    uint64_t generation=0, observed_ns=0;
    uint32_t local_id=0,owner_id=0,queue=0,manager=0;
    uint32_t remote=0,local=0,last_used=0;
    bool role_known=false,unsupported=false,seen=false,pending=false,used=false,advanced=false;
    void reset() {const auto next=generation+1;*this={};generation=next;}
    void role(uint32_t id,uint32_t owner) {
        if(role_known && (id!=local_id || owner!=owner_id))reset();
        role_known=true;local_id=id;owner_id=owner;
    }
    bool invited() const {return role_known && local_id && owner_id && local_id!=owner_id && !unsupported;}
    void observe(uint32_t q,uint32_t m,uint32_t r,uint32_t l,uint64_t now,bool active) {
        if(!invited()) {pending=false;return;}
        if(!q || !m || !active) {pending=false;return;}
        if(queue && (queue!=q || manager!=m)) {pending=false;unsupported=true;return;}
        queue=q;manager=m;
        if(seen && !newer(r,remote)) {
            // Duplicate/reordered data cannot refresh a request's lifetime.
            // Same host frame with a changed local frame invalidates it.
            if(r!=remote || l!=local)pending=false;
            if(uint32_t(r-remote)==0x80000000u)unsupported=true;
            return;
        }
        remote=r;local=l;observed_ns=now;seen=true;pending=false;
    }
    void request(uint32_t q,uint32_t r,uint32_t l,uint64_t now) {
        if(!invited() || !seen || q!=queue || r!=remote || l!=local ||
           uint32_t(r-l)!=1 || now<observed_ns || now-observed_ns>freshness_ns ||
           (used && !newer(r,last_used))) {pending=false;return;}
        pending=true;advanced=false;
    }
    void advance(uint32_t m,uint32_t current_local) {
        // The title advances before its update/render decision. Bind the
        // request to exactly that advance, never to a later caught-up frame.
        if(!pending)return;
        if(advanced || m!=manager || current_local!=remote) {pending=false;return;}
        advanced=true;
    }
    bool consume(uint32_t current_local,uint64_t now,bool enabled,bool skipped) {
        const bool accept=pending && invited() && enabled && skipped &&
            now>=observed_ns && now-observed_ns<=freshness_ns &&
            advanced && current_local==remote && uint32_t(remote-local)==1 &&
            (!used || newer(remote,last_used));
        pending=false;
        if(accept) {last_used=remote;used=true;}
        return accept;
    }
};
}
