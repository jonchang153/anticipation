"""
Utilities for operating on encoded Midi sequences.
"""

from collections import defaultdict

from anticipation.config import *
from anticipation.vocab import *
from anticipation.vocabs.tripletmidi import vocab


#
# TODO: need to rewrite this whole module to take a vocabulary parameter
#


def print_tokens(tokens):
    print('---------------------')
    for j, (tm, dur, note) in enumerate(zip(tokens[0::3],tokens[1::3],tokens[2::3])):
        if note == vocab['separator']:
            assert tm == SEPARATOR and dur == SEPARATOR
            print(j, 'SEPARATOR')
            continue

        if note == vocab['rest']:
            assert tm < CONTROL_OFFSET
            assert dur == DUR_OFFSET+0
            print(j, tm, 'REST')
            continue

        if note < vocab['control_offset']:
            tm = tm - TIME_OFFSET
            dur = dur - DUR_OFFSET
            note = note - NOTE_OFFSET
            instr = note//2**7
            pitch = note - (2**7)*instr
            print(j, tm, dur, instr, pitch)
        else:
            tm = tm - ATIME_OFFSET
            dur = dur - ADUR_OFFSET
            note = note - ANOTE_OFFSET
            instr = note//2**7
            pitch = note - (2**7)*instr
            print(j, tm, dur, instr, pitch, '(A)')


def print_training_tokens(tokens):
    print('j time dur instr pitch')
    print('---------------------')
    tokens = tokens[1:] # eat pad token
    for j, (tm, dur, note) in enumerate(zip(tokens[0::3],tokens[1::3],tokens[2::3])):
        if note == vocab['separator']:
            assert tm == SEPARATOR and dur == SEPARATOR
            print(j, 'SEPARATOR')
            continue

        if note == vocab['rest']:
            assert tm < CONTROL_OFFSET
            assert dur == DUR_OFFSET+0
            print(j, tm, 'REST')
            continue

        if note < vocab['control_offset']:
            tm = tm - vocab['time_offset']
            dur = dur - vocab['duration_offset']
            note = note - vocab['note_offset']
            instr = note//2**7
            pitch = note - (2**7)*instr
            print(j, tm, dur, instr, pitch)
            continue
            
        if vocab['control_offset'] < note < vocab['special_offset']:
            tm = tm - ATIME_OFFSET
            dur = dur - ADUR_OFFSET
            note = note - ANOTE_OFFSET
            instr = note//2**7
            pitch = note - (2**7)*instr
            print(j, tm, dur, instr, pitch, '(A)')
            continue
        else:
            assert tm >= vocab['special_offset'] and dur >= vocab['special_offset']
            print(j, 'CONTROL PREFIX')
            for tok in [tm, dur, note]:
                if tok > vocab['human_instrument_offset']:
                    print('Instr: ', tok - vocab['human_instrument_offset'], '(H)')
                elif tok not in [vocab['pad'], vocab['separator'], vocab['task']['autoregress'], vocab['task']['anticipate']]:
                    print('Instr: ', tok - vocab['instrument_offset'])
            if note == vocab['task']['autoregress']:
                print('Task:   autoregress')
            elif note == vocab['task']['anticipate']:
                print('Task:   anticipate')
            print('~~~~~~~~~~~~~~~~~~~~~')
                
            


def clip(tokens, start, end, clip_duration=True, seconds=True):
    if seconds:
        start = int(TIME_RESOLUTION*start)
        end = int(TIME_RESOLUTION*end)

    new_tokens = []
    for (time, dur, note) in zip(tokens[0::3],tokens[1::3],tokens[2::3]):
        if note < CONTROL_OFFSET:
            this_time = time - TIME_OFFSET
            this_dur = dur - DUR_OFFSET
        else:
            this_time = time - ATIME_OFFSET
            this_dur = dur - ADUR_OFFSET

        if this_time < start or end < this_time:
            continue

        # truncate extended notes
        if clip_duration and end < this_time + this_dur:
            dur -= this_time + this_dur - end

        new_tokens.extend([time, dur, note])

    return new_tokens


def mask(tokens, start, end):
    new_tokens = []
    for (time, dur, note) in zip(tokens[0::3],tokens[1::3],tokens[2::3]):
        if note < CONTROL_OFFSET:
            this_time = (time - TIME_OFFSET)/float(TIME_RESOLUTION)
        else:
            this_time = (time - ATIME_OFFSET)/float(TIME_RESOLUTION)

        if start < this_time < end:
            continue

        new_tokens.extend([time, dur, note])

    return new_tokens


def delete(tokens, criterion):
    new_tokens = []
    for token in zip(tokens[0::3],tokens[1::3],tokens[2::3]):
        if criterion(token):
            continue

        new_tokens.extend(token)

    return new_tokens


def sort(tokens):
    """ sort sequence of events or controls (but not both) """

    times = tokens[0::3]
    indices = sorted(range(len(times)), key=times.__getitem__)

    sorted_tokens = []
    for idx in indices:
        sorted_tokens.extend(tokens[3*idx:3*(idx+1)])

    return sorted_tokens


def split(tokens):
    """ split a sequence into events and controls """

    events = []
    controls = []
    for (time, dur, note) in zip(tokens[0::3],tokens[1::3],tokens[2::3]):
        if note < CONTROL_OFFSET:
            events.extend([time, dur, note])
        else:
            controls.extend([time, dur, note])

    return events, controls


def pad(tokens, end_time=None, density=TIME_RESOLUTION):
    end_time = TIME_OFFSET+(end_time if end_time else max_time(tokens, seconds=False))
    new_tokens = []
    previous_time = TIME_OFFSET+0
    for (time, dur, note) in zip(tokens[0::3],tokens[1::3],tokens[2::3]):
        # must pad before separation, anticipation
        assert note < CONTROL_OFFSET

        # insert pad tokens to ensure the desired density
        while time > previous_time + density:
            new_tokens.extend([previous_time+density, DUR_OFFSET+0, REST])
            previous_time += density

        new_tokens.extend([time, dur, note])
        previous_time = time

    while end_time > previous_time + density:
        new_tokens.extend([previous_time+density, DUR_OFFSET+0, REST])
        previous_time += density

    return new_tokens


def unpad(tokens):
    new_tokens = []
    for (time, dur, note) in zip(tokens[0::3],tokens[1::3],tokens[2::3]):
        if note == REST: continue

        new_tokens.extend([time, dur, note])

    return new_tokens


def anticipate(events, controls, delta=DELTA*TIME_RESOLUTION):
    """
    Interleave a sequence of events with anticipated controls.

    Inputs:
      events   : a sequence of events
      controls : a sequence of time-localized controls
      delta    : the anticipation interval
    
    Returns:
      tokens   : interleaved events and anticipated controls
      controls : unconsumed controls (control time > max_time(events) + delta)
    """

    if len(controls) == 0:
        return events, controls

    tokens = []
    event_time = 0
    control_time = controls[0] - ATIME_OFFSET
    for time, dur, note in zip(events[0::3],events[1::3],events[2::3]):
        while event_time >= control_time - delta:
            tokens.extend(controls[0:3])
            controls = controls[3:] # consume this control
            control_time = controls[0] - ATIME_OFFSET if len(controls) > 0 else float('inf')

        assert note < CONTROL_OFFSET
        event_time = time - TIME_OFFSET
        tokens.extend([time, dur, note])

    return tokens, controls

def anticipate_and_anti_anticipate(events, chord_controls, human_controls, chord_delta=DELTA*TIME_RESOLUTION, human_delta=HUMAN_DELTA*TIME_RESOLUTION):
    """
    Interleave a sequence of events with anticipated controls.

    Inputs:
      events         : a sequence of events
      chord_controls : a sequence of time-localized controls
      chord_delta    : the anticipation interval
      human_controls : a sequence of time-localized controls
      human_delta    : the anti-anticipation interval
    
    Returns:
      tokens         : interleaved events and anticipated controls
      chord_controls : unconsumed chord controls (control time > max_time(events) + delta)
      human_controls : unconsumed human controls (control time > max_time(events) + delta)

    """

    if len(chord_controls) == 0:
        events, human_controls = anticipate(events, human_controls, human_delta)
        return events, chord_controls, human_controls
    if len(human_controls) == 0:
        events, chord_controls = anticipate(events, chord_controls, chord_delta)
        return events, chord_controls, human_controls

    tokens = []
    event_time = 0
    chord_control_time = chord_controls[0] - ATIME_OFFSET
    human_control_time = human_controls[0] - ATIME_OFFSET
    for time, dur, note in zip(events[0::3],events[1::3],events[2::3]):
        # while there is a control before the event:
        while (event_time >= chord_control_time - chord_delta) or (event_time >= human_control_time - human_delta):
            # insert controls in sort order
            if (chord_control_time - chord_delta <= human_control_time - human_delta):
                tokens.extend(chord_controls[0:3])
                chord_controls = chord_controls[3:] # consume this control
                chord_control_time = chord_controls[0] - ATIME_OFFSET if len(chord_controls) > 0 else float('inf')
            else:
                tokens.extend(human_controls[0:3])
                human_controls = human_controls[3:] # consume this control
                human_control_time = human_controls[0] - ATIME_OFFSET if len(human_controls) > 0 else float('inf')
   
        # second option: chord first convention:
        # event_{t-1}         chords_controls in order, then human_controls in order          event_{t}

        assert note < CONTROL_OFFSET
        event_time = time - TIME_OFFSET
        tokens.extend([time, dur, note])

    return tokens, chord_controls, human_controls


def sparsity(tokens):
    max_dt = 0
    previous_time = TIME_OFFSET+0
    for (time, dur, note) in zip(tokens[0::3],tokens[1::3],tokens[2::3]):
        if note == SEPARATOR: continue
        assert note < CONTROL_OFFSET # don't operate on interleaved sequences

        max_dt = max(max_dt, time - previous_time)
        previous_time = time

    return max_dt

def remove_prefix(tokens, return_index=False):
    prefix_vocab = [vocab['pad']] + [vocab['separator']] + list(range(vocab['instrument_offset'], vocab['instrument_offset'] + vocab['config']['max_instrument'])) + list(range(vocab['human_instrument_offset'], vocab['human_instrument_offset'] + vocab['config']['max_instrument'])) + [vocab['task']['anticipate']] + [vocab['task']['autoregress']]
    
    if (tokens[0] in prefix_vocab) and (tokens[1] in list(range(vocab['duration_offset'], vocab['duration_offset'] + vocab['config']['max_duration']))):
        # time token overflow, return tokens
        if return_index:
            return 0
        else:
            return tokens
    
    for i, tok in enumerate(tokens):
        if tok not in prefix_vocab:
            if i == 0:
                if return_index:
                    return i
                else:
                    return tokens
            else:
                assert(tokens[i-1] in [vocab['task']['anticipate'], vocab['task']['autoregress']])
                # if this isn't satisfied, there is probably overflow into the control block
                if return_index:
                    return i
                else:
                    return tokens[i:]
    
    return tokens

def min_time(tokens, seconds=True, instr=None):
    mt = None

    # skip the first control block
    tokens = remove_prefix(tokens)
    
    for time, dur, note in zip(tokens[0::3],tokens[1::3],tokens[2::3]):
        # stop calculating at sequence separator
        if note >= SPECIAL_OFFSET: break

        if note < CONTROL_OFFSET:
            time -= TIME_OFFSET
            note -= NOTE_OFFSET
        else:
            time -= ATIME_OFFSET
            note -= ANOTE_OFFSET

        # min time of a particular instrument
        if instr is not None and instr != note//2**7:
            continue

        mt = time if mt is None else min(mt, time)

    if mt is None: mt = 0
    return mt/float(TIME_RESOLUTION) if seconds else mt


def max_time(tokens, seconds=True, instr=None):
    mt = 0
    for time, dur, note in zip(tokens[0::3],tokens[1::3],tokens[2::3]):
        # skip the control block
        if note >= SPECIAL_OFFSET: continue

        if note < CONTROL_OFFSET:
            time -= TIME_OFFSET
            note -= NOTE_OFFSET
        else:
            time -= ATIME_OFFSET
            note -= ANOTE_OFFSET

        # max time of a particular instrument
        if instr is not None and instr != note//2**7:
            continue

        mt = max(mt, time)

    return mt/float(TIME_RESOLUTION) if seconds else mt


def get_instruments(tokens):
    instruments = defaultdict(int)
    for time, dur, note in zip(tokens[0::3],tokens[1::3],tokens[2::3]):
        if note >= SPECIAL_OFFSET: continue

        if note < CONTROL_OFFSET:
            note -= NOTE_OFFSET
        else:
            note -= ANOTE_OFFSET

        instr = note//2**7
        instruments[instr] += 1

    return instruments


def translate(tokens, dt, seconds=False):
    if seconds:
        dt = int(TIME_RESOLUTION*dt)

    # ignore first prefix:
    start_idx = remove_prefix(tokens, return_index=True)
    new_tokens = tokens[:start_idx]
    tokens = tokens[start_idx:]
    
    for (time, dur, note) in zip(tokens[0::3],tokens[1::3],tokens[2::3]):
        # stop translating after EOT, i.e. at next prefix
        if note >= vocab['special_offset']:
            new_tokens.extend([time, dur, note])
            dt = 0
            continue

        if note < vocab['control_offset']:
            this_time = time - vocab['time_offset']
        else:
            this_time = time - vocab['atime_offset']

        assert 0 <= this_time + dt
        new_tokens.extend([time+dt, dur, note])

    return new_tokens

def combine(events, controls):
    return sort(events + [token - CONTROL_OFFSET for token in controls])
