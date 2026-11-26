#pragma once
#include <cstdint>

// Experimental provenance tracking for a phase estimate, not a duration queue.
// No estimate can be invalidated until every nonzero component has a known
// source frame and matches the title's current value.
namespace tb_pacing_recovery {
struct Tag { uint32_t frame=0; bool known=false; };
inline bool newer(uint32_t a,uint32_t b) {
    const uint32_t distance=a-b;
    return distance && distance<0x80000000u;
}
inline Tag combine(Tag a,Tag b) {
    if(!a.known || !b.known)return {};
    if(a.frame==b.frame)return a;
    if(newer(a.frame,b.frame))return a;
    if(newer(b.frame,a.frame))return b;
    return {};
}
struct State {
    Tag pending,retained,field;
    uint32_t pending_value=0,retained_value=0,field_value=0,last_recovery=0;
    bool recovered=false;
    void sample(uint32_t frame,uint32_t difference,uint32_t current_pending) {
        if(current_pending!=pending_value)pending={};
        if(!difference || difference>=0x80000000u || difference<current_pending)return;
        Tag next{frame,true};
        pending=difference==current_pending && current_pending?combine(pending,next):next;
        pending_value=difference;
    }
    void select(uint32_t current_pending,uint32_t current_retained) {
        if(current_pending!=pending_value || !current_pending)pending={};
        if(current_retained!=retained_value || !current_retained)retained={};
        retained=current_pending>current_retained?pending:
                 current_retained>current_pending?retained:combine(pending,retained);
        retained_value=current_pending>current_retained?current_pending:current_retained;
        pending={};pending_value=0;
    }
    void publish(uint32_t value,uint32_t current_retained) {
        if(!value || value!=current_retained || retained_value<=512 ||
           retained_value>>1!=value)retained={};
        retained_value=current_retained;field=retained;field_value=value;
    }
    bool recover(uint32_t remote,uint32_t local,uint32_t current_field,
                 uint32_t current_pending,uint32_t current_retained) {
        if(!newer(remote,local) || (recovered && !newer(remote,last_recovery)))return false;
        if(!current_field || current_field!=field_value || !field.known ||
           !newer(remote,field.frame))return false;
        if(current_pending && (current_pending!=pending_value || !pending.known ||
                              !newer(remote,pending.frame)))return false;
        if(current_retained && (current_retained!=retained_value || !retained.known ||
                               !newer(remote,retained.frame)))return false;
        pending={};retained={};field={};pending_value=retained_value=field_value=0;
        last_recovery=remote;recovered=true;
        return true;
    }
};
}
