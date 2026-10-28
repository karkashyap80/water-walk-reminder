# Water + Walk Reminder

Two separate break reminders for the Linux desktop: **drink water** and **walk for 2 minutes**.

A small card rises up the right side of the screen, settles in the top-right corner and stays until you click **Done**. Water and walk each have their own schedule. When both are due at the same time, one card shows both.

- One Python file (`reminder.py`), GTK 3, no network access, no data collected
- Its own schedule per reminder: on/off, first and last hour, how often (15–120 min), which days
- Settings window opens from the ⚙ button on the card (or `reminder.py --settings`)
- A systemd user timer checks every 15 minutes. A card only shows when a reminder is due.

Defaults: water every 30 min, walk every 60 min, 11:00 AM to 8:00 PM, every day.

## Requirements

Ubuntu/Debian (tested on Ubuntu 24.04, GNOME 46):

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0
```

Wayland users: the card runs through XWayland (`GDK_BACKEND=x11`) so it can place itself in the corner.

## Try it

```bash
python3 reminder.py --test          # water + walk card now
python3 reminder.py --test water    # water card only (or: --test walk)
python3 reminder.py --settings      # open the settings window
python3 reminder.py --bottom        # settle bottom-right instead of top-right
```

## Install (runs automatically every 15 min)

```bash
mkdir -p ~/.local/share/water-walk-reminder ~/.config/systemd/user
cp reminder.py ~/.local/share/water-walk-reminder/
cp systemd/water-walk-reminder.service systemd/water-walk-reminder.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now water-walk-reminder.timer
```

Check it: `systemctl --user list-timers | grep water`

## Settings

Saved to `~/.config/water-walk-reminder.json`:

```json
{
  "water": {"on": true, "start_hour": 11, "end_hour": 20, "every_minutes": 30, "days": [0,1,2,3,4,5,6]},
  "walk":  {"on": true, "start_hour": 11, "end_hour": 20, "every_minutes": 60, "days": [0,1,2,3,4,5,6]}
}
```

Days: 0 = Monday … 6 = Sunday. `every_minutes` should be a multiple of 15 (the timer tick).

## Uninstall

```bash
systemctl --user disable --now water-walk-reminder.timer
rm ~/.config/systemd/user/water-walk-reminder.{service,timer}
rm -r ~/.local/share/water-walk-reminder
rm -f ~/.config/water-walk-reminder.json
systemctl --user daemon-reload
```

## Licence

MIT. See [LICENSE](LICENSE).
