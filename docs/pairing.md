# Pairing guide

Pairing teaches your cover's receiver to accept commands from the Centronic
USB stick, exactly like adding an additional handheld remote. The stick never
replaces your master remote - keep using it as before.

## Before you start

- The integration must be set up and show **Connected** (the stick entry on
  the integration page).
- You need physical access to the cover's **master remote** (the remote or
  wall transmitter that was originally programmed to the cover).
- Be within RF range of the cover.

## Pairing a cover

1. Open **Settings → Devices & services → Becker Centronic** and press
   **Add cover**.
2. Enter a name and accept the suggested **unit/channel** (or pick your own -
   each cover needs its own combination). Choose the cover type.
3. The wizard now asks you to put the cover into pairing mode:
   **press and hold the program button on the master remote** until the cover
   confirms with a single *clack*. On most remotes the program button is a
   small recessed button on the back - use a paper clip.
4. Press **Submit** in the wizard within a few seconds. The stick transmits
   the pairing sequence.
5. Listen: a successful pairing is confirmed by a single *clack* followed by
   a double *clack-clack*.
   - **Confirmed?** Choose *"Yes - the cover confirmed"* and finish the
     settings page (travel times etc. - everything is optional and can be
     changed later).
   - **Nothing happened?** Choose *retry with the same sequence* (put the
     cover into pairing mode again first!), or *try an alternative pairing
     sequence* - receivers from different production years accept slightly
     different pairing telegrams, and the wizard knows three variants.

## Tips

- **One cover at a time.** Only the cover whose receiver is in pairing mode
  will react.
- **Pairing mode times out** after a few seconds. If you waited too long,
  hold the program button again before retrying.
- **Group control**: if you want several covers to move together, pair each
  cover on its own channel and group them in Home Assistant (cover groups).
  This keeps individual control possible.
- If you migrated from a previous installation and imported
  `centronic-stick.db`, your covers are already paired - just add them with
  the *"already paired"* checkbox enabled and the same unit/channel they used
  before.

## Unpairing

To remove the stick from a cover's paired senders:

1. Put the cover into pairing mode with the master remote (single *clack*).
2. Call the `becker_centronic.unpair` action targeting the cover entity
   (Developer tools → Actions).
3. The cover confirms with *clack* and *clack-clack*.

Then delete the cover from the integration page.

## Advanced

- `becker_centronic.pair_master` registers the stick as the cover's *master*
  sender. Only needed for fresh receivers without any master remote - not
  recommended otherwise.
- `becker_centronic.clear_position` clears the end positions stored in the
  receiver. Only use it if you know what you are doing; the cover will need
  its end positions re-programmed afterwards.
