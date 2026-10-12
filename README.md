# Morning Edition

Morning Edition is a self-hosted personal newspaper, written with Claude and printed every morning. Each edition combines your calendar, favorite feeds, and news stories curated by Claude into a few pages printed on both sides, always an even number of pages. A typical month of daily editions costs about $6.50 with Claude Sonnet 5.5 (the default) or about $13 with Claude Opus 5.5, depending on which Claude model you choose.

## Requirements

- **Docker**: Morning Edition runs in a container; Docker Desktop or Docker Engine is required.
- **Network printer**: An AirPrint-compatible printer or a printer that supports IPP Everywhere protocol. The printer must be reachable on your local network.
- **Anthropic API key**: Required for Claude to curate and write stories. Without it, the paper prints feed teasers only.

## Setup

1. Clone the repository and enter the directory:
   ```bash
   git clone https://github.com/rickgram/morning-edition.git
   cd morning-edition
   ```

2. Create a `.env` file from the example and fill in your settings:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and set `DASHBOARD_PASSWORD` to a memorable password and `ANTHROPIC_API_KEY` to your Anthropic API key. Set `WATCHTOWER_TOKEN` to a long random string (see [Updates](#updates)). The `PORT` and `TZ` settings are optional (defaults are 8080 and UTC). Generate a strong `WATCHTOWER_TOKEN`, and optionally a `SESSION_SECRET`, with:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

3. Start the containers:
   ```bash
   docker compose up -d
   ```
   This pulls the published Morning Edition image and the updater container. To build the image from the source in this folder instead, run `docker compose up -d --build`.

4. Open the dashboard in your browser:
   ```
   http://<host>:8080
   ```
   Replace `<host>` with your machine's IP address or hostname, or use `localhost:8080` if running locally. If you set a custom `PORT` in `.env`, use that instead of 8080.

5. Configure your paper in Settings:
   - **Paper name**: Appears in the masthead. The masthead font picker shows your paper name in each of the four fonts as you type, so you can see the choice before saving
   - **Weather**: Your home location (sets the weather and the town shown on the masthead) and whether to use US units (Fahrenheit, mph, inches, miles) or metric (Celsius, km/h, millimeters, kilometers)
   - **Timezone**: Pick from the dropdown (United States zones first; start typing to jump to a name). Used for scheduling and calendar interpretation
   - **Print time**: What time each morning the paper should print
   - **Claude model**: Sonnet 5.5 is the default and the recommended choice (about $6.50 a month); Opus 5.5 costs about twice that. Switch any time in Settings under "Printer and writing"
   - **Printer address**: The IP address of your printer (e.g., `192.168.1.50`). Find your printer's IP address in its network settings or your router's device list.

Then add your feeds in the Feeds section of Settings, and optionally connect one or more calendars.

## Calendar Integration

To print your calendar on the first page, get your calendar's private iCal link:

1. Go to [Google Calendar Settings](https://calendar.google.com/calendar/u/0/r/settings)
2. Select your calendar
3. Under "Integrate calendar," copy the secret address in iCal format (the URL starting with `https://calendar.google.com/calendar/ical/`)
4. In the Dashboard, open Settings, find the Calendars section, give the calendar a name, paste the link, and choose Add calendar

Repeat for as many calendars as you like (work, family, a boat club). Events from all of them are merged into one "Your Day" list on the front page, sorted with all-day events first, then by time. The dashboard lists each calendar by name and says "link saved"; it never shows the link again. Use Remove to delete one. The block also lists tomorrow's events under a small "Tomorrow" heading when there is room; if space runs short, tomorrow's events are the first thing trimmed ("and N more tomorrow"), then the outlook days, then today's events. If one calendar fails to load, the others still print and the edition log notes which one failed (by name only).

Warning: anyone with this link can read your calendar. Keep it private and regenerate it if you share your device.

## Expanded Weather

The front page always shows today's high and low, the wind, and a three-day outlook, each day with a small line drawing of the weather (sun, cloud, rain, snow, thunderstorm and so on). In Settings, under "Weather," tick "Expanded weather" to add a full-width Weather band at the top of page 2 (the setting is off by default):

- Today: sunrise and sunset, moon phase and how much of it is lit, rain over the past 24 hours, humidity, pressure, visibility, the day's peak UV index, and air quality (US AQI)
- A five-day forecast row with a small weather drawing, highs, lows, and the chance of precipitation
- The moon drawn as it looks tonight, lit side growing or shrinking

Weather and air quality come from the free [Open-Meteo](https://open-meteo.com/) service (no key needed); the moon phase is calculated locally. If the air quality service is down, that one box prints "n/a" and everything else still prints. The band needs room on an inside page, so it appears only when "Most sheets on a heavy news day" is 2 or more. Page 2's story columns start below it, and the paper still always prints an even number of pages.

In metric mode the band shows rain in millimeters, pressure in hPa, and visibility in kilometers.

## Feeds

Feeds live in the Feeds section of the Settings page. You can create sections by any name (the default is Local, World, and Tech). A feed assigned to the "Good News" section supplies the back-page story slot only.

Most news sites and blogs publish an RSS feed. Here are a few that work well:

- https://feeds.bbci.co.uk/news/world/rss.xml (BBC World News)
- https://feeds.arstechnica.com/arstechnica/index (Ars Technica)
- https://www.goodnewsnetwork.org/feed/ (Good News Network)
- https://reasonstobecheerful.world/feed/ (Reasons to Be Cheerful)

Google News feeds are marked "(headlines only)" in the feed row. Because Google News links point to aggregated content rather than original articles, those feeds print as one-line headlines only. You can find a custom Google News feed for any topic at https://news.google.com/.

### Finding feeds

Not sure where to find feeds? Press "Find feeds for me" in the Feeds section of Settings (or on the Today page while you have none). Claude searches the web for publications that match your interests, your home area, and your sections, plus a few Good News outlets. The app then fetches every candidate itself and keeps only feeds that work and have stories from the last week. You review the list, change sections if you like, and add the ones you want. Sites that block full-text reading are added as headlines only. This needs `ANTHROPIC_API_KEY` in `.env`, and a search costs a few cents and takes a minute or two.

## Updates

Morning Edition is published as a ready-made image at `ghcr.io/rickgram/morning-edition` (for both Intel/AMD and ARM machines). Every change that lands on the main branch passes the tests and is built and published automatically, so an update is just "pull the new image and restart".

The compose file runs a second container, [Watchtower](https://github.com/nicholas-fedor/watchtower) (the maintained fork), that does the pulling and restarting when Morning Edition asks it to:

- The updater has access to the Docker socket by design; that is how it can restart containers. Treat it like any container with that access.
- Its API requires the `WATCHTOWER_TOKEN` from your `.env`, and it is not published outside the compose network (it has no `ports:`), so only the Morning Edition container can reach it.
- It only touches containers carrying the `com.centurylinklabs.watchtower.enable=true` label, which only the Morning Edition container has. Other containers on the same machine are left alone.
- It has no schedule of its own; it updates only when asked.

In the dashboard, open Settings and find the Updates section:

- **Check for updates** asks whether a newer image is published and shows the latest change.
- **Update now** pulls the new image and restarts the dashboard, which is unavailable for about a minute. It refuses while an edition is building.
- **Update automatically overnight** checks at the time you choose (in your paper's timezone) and updates only if there is something new. It skips the night, and says why, if an edition is building or the next edition builds within the hour. The result of the last overnight run shows in the Updates section.

If you prefer to update by hand, run this in the folder that holds `docker-compose.yml`:

```bash
docker compose pull && docker compose up -d
```

Things to know:

- `docker compose down` and `docker compose logs` also need `WATCHTOWER_TOKEN` set in `.env`, because the compose file refuses to load without it.
- If you built the image yourself with `docker compose up -d --build`, the next update replaces it with the published `latest` image from the registry.
- A print that is still in progress when you update may be cut off, so wait for it to finish first.
- Maintainers: a new GitHub Container Registry package starts out private. After the first publish, open the package's settings on GitHub and change its visibility to public, otherwise anonymous pulls (and the updater) fail.

### Switching an existing install to published images

If you set Morning Edition up before images were published (it was built from this folder):

1. Pull the new compose file: `git pull`
2. Add `WATCHTOWER_TOKEN` to `.env`, using the generator command in Setup above.
3. Run `docker compose pull && docker compose up -d`

Your settings and editions live in `./data` and carry over unchanged.

## Troubleshooting

**Container keeps restarting**
Run `docker compose logs` to check for errors. The most common cause is a missing `DASHBOARD_PASSWORD` in `.env`.

**`docker compose` says WATCHTOWER_TOKEN is missing**
Add a `WATCHTOWER_TOKEN` line to `.env` (see Setup).

**Test print fails or prints blank**
- Double-check the printer address is correct
- Confirm the printer is powered on and reachable on the network
- Set a DHCP reservation for your printer on your router so its address never changes

**For older AirPrint-only printers**
If you see "no suitable driver" errors from `lpadmin -m everywhere`, run:
```bash
docker compose exec morning-edition lpinfo -m | grep driverless
```
and open a GitHub issue with the output.

## Fonts

Morning Edition includes these fonts (all SIL Open Font License):

- **Headlines**: Libre Caslon Text
- **Body text**: Source Serif 4
- **Labels** (section names, datelines, small print): Libre Franklin
- **Masthead**: your choice in Settings, from Pirata One, Manufacturing Consent, Grenze Gotisch, and Playfair Display

## Where Data Lives

The `./data` directory contains a SQLite database with your settings, feeds, and edition history, plus a PDF file for each printed edition. Back it up regularly.

## License

Morning Edition is licensed under the MIT License. Bundled fonts are under the SIL Open Font License (OFL). The word list is MIT.
