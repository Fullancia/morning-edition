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
   Edit `.env` and set `DASHBOARD_PASSWORD` to a memorable password and `ANTHROPIC_API_KEY` to your Anthropic API key. The `PORT` and `TZ` settings are optional (defaults are 8080 and UTC). Optionally, generate a strong `SESSION_SECRET` with:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

3. Start the container:
   ```bash
   docker compose up -d --build
   ```

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

Repeat for as many calendars as you like (work, family, a boat club). Events from all of them are merged into one "Your Day" list on the front page, sorted with all-day events first, then by time. The dashboard lists each calendar by name and says "link saved"; it never shows the link again. Use Remove to delete one. If one calendar fails to load, the others still print and the edition log notes which one failed (by name only).

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

## Troubleshooting

**Container keeps restarting**
Run `docker compose logs` to check for errors. The most common cause is a missing `DASHBOARD_PASSWORD` in `.env`.

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
