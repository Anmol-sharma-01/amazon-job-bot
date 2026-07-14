# Official Career Page Job Alert Bot

This project sends Telegram alerts for matching jobs found on official employer career pages.

## What it monitors

### Every 15 minutes
- Amazon hourly warehouse/driver roles
- UPS warehouse and driver roles
- FedEx package-handler/warehouse and driver roles
- Purolator warehouse, sorter, courier, and driver roles

### Every 2 hours
- Entry-level IT support and technical support roles
- Entry-level cybersecurity and SOC/security analyst roles
- Amazon, FedEx, Purolator, BlackBerry, Arctic Wolf, OpenText, and eSentire official career pages

## Geographic filter

The bot includes Kitchener, Waterloo, Cambridge, Guelph, Brantford, Woodstock,
Hamilton, Burlington, Oakville, Milton, Mississauga, Brampton, Caledon, Vaughan,
Toronto, Etobicoke, North York, Scarborough, Markham, Richmond Hill, and nearby
areas. Remote Ontario/Canada roles are allowed for IT support and cybersecurity.

## Installation in GitHub

1. Delete the old `.github/workflows/amazon-bot.yml` file so it does not keep running.
2. Upload all files and folders from this package to the repository root.
3. Keep these GitHub Actions secrets:
   - `BOT_TOKEN`
   - `CHAT_ID`
4. Open **Actions** and manually run:
   - `Logistics Job Alerts`
   - `IT and Cyber Job Alerts`
5. Check Telegram and the workflow logs.

## Important behaviour

- The first run sends at most 15 current matches and records the rest.
- Future runs send only jobs not seen before.
- A status message is sent on manual runs and approximately once per day when no new job appears.
- Diagnostic JSON files are available under the workflow run's **Artifacts** section.
- Only links discovered from the listed official career pages are sent.

## Add another official employer

Add an object to `sources.json`:

```json
{
  "name": "Employer Career Page",
  "company": "Employer",
  "group": "tech",
  "url": "https://official-employer-site.example/jobs",
  "categories": ["it_support", "cyber"],
  "regional_page": false,
  "max_pages": 3,
  "wait_ms": 5000
}
```

Valid categories are `warehouse`, `driver`, `it_support`, and `cyber`.

## Limitations

Career sites change layouts and may block automated browsers. No scraper can guarantee
every vacancy from every employer. Review the diagnostic artifact if a source reports an error.
Amazon hourly pages are especially dynamic, so also enable Amazon's official job alerts as a backup.
