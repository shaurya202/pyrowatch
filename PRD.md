
Overview

Build an app that tracks possible wildfires near places a user saves, such as their home, college, or family members’ addresses. The app pulls recent satellite fire detections, plots them on a map, checks the weather and air quality around them, and sends an alert when a new detection appears nearby or conditions get worse.

Getting fire detections from NASA

NASA’s FIRMS system publishes thermal anomalies detected by satellites. Each detection includes its coordinates, the time it was recorded, the satellite that recorded it, a confidence level, and fire radiative power, which measures how much heat the satellite detected.

Use one of the VIIRS near-real-time feeds. VIIRS detects heat at about 375-meter resolution, so each point represents the center of a 375-meter pixel where unusual heat was found. NASA currently provides VIIRS data from Suomi NPP, NOAA-20, and NOAA-21. You can start with one feed or combine all three to get more frequent observations.

The FIRMS Area API lets you request detections inside a set of map coordinates from the past one to five days. You’ll need a free FIRMS MAP_KEY to use it.

When someone saves a location, convert the address into latitude and longitude. For a US-focused app, you can use the Census Geocoder. Create a box around the saved coordinates, request the FIRMS detections inside it, and then calculate the exact distance from each detection to the saved location.

One fire can create several nearby detections, and multiple satellites can detect the same fire. Group detections that appear close together within a similar period instead of treating every point as a separate fire. Each group can store its center, first detection, latest detection, number of detections, highest confidence, and strongest fire radiative power.

Adding weather and air quality

Use the National Weather Service API to get wind speed, wind direction, and relative humidity around each group of fire detections. Send the group’s coordinates to the `/points/{latitude},{longitude}` endpoint. The response gives you a `forecastGridData` URL containing weather values for that location over time.

Wind direction tells you where the wind is coming from. Add 180 degrees to get the direction it’s moving toward, then compare that direction with the location of the user. This tells you whether the saved location is roughly downwind from the detection. Wind speed shows how strongly the air is moving, while humidity provides more context about how dry the surrounding conditions are.

Use EPA AirNow data to check the air quality near the saved location. The easier option is its current-observations API, which returns the AQI for a reporting area. If you want actual PM2.5 concentrations from individual monitoring sites, AirNow also publishes hourly files containing each monitor’s coordinates, observation time, PM2.5 level, and AQI.

Match weather and air-quality readings using both location and time. Keep every timestamp in UTC and use the reading closest to the time of the fire detection or alert calculation. Store the update time from every source so the app can show users how recent the information is.

Calculating the score

Create a score using the detection’s distance, age, confidence, fire radiative power, number of repeated detections, wind direction, wind speed, humidity, and nearby PM2.5 level.

Distance and time should matter the most. A recent detection five miles away should affect the score more than an older detection fifty miles away. The score can increase further if several satellites detect heat in the same area, the location is downwind, or nearby PM2.5 levels are elevated.

Show the reasons behind the score. For example, the app could say that a location received a higher score because a recent detection appeared nearby, the location was downwind, and PM2.5 had increased. If weather or air-quality data is missing, mark the score as having limited data instead of treating the missing value as zero.

This is your own context score, not an official wildfire danger rating. Don’t present it as a prediction that a fire will reach someone’s location.

Alerts and the timeline

Let users choose an alert radius for each saved location. Send an alert when a new group of detections appears inside that radius, when another satellite detects activity in the same area, or when the score increases by a meaningful amount.

Group repeated detections into the same alert so users don’t get a new notification every time a satellite passes over the fire. Each alert should include the detection time, approximate distance, confidence, data source, and the reason it was triggered.

Store the raw detections and place them on a 24-hour timeline using their acquisition times. Users can replay the timeline to see whether satellite detections around an area are becoming more or less frequent. Call this a change in detected activity, not confirmed fire spread, because cloud cover and satellite schedules can change how many points appear.

Adding official wildfire information

For a more complete US version, connect the detections to the National Interagency Fire Center’s WFIGS data. WFIGS publishes current wildfire incidents and available fire perimeters. It can add the official incident name, perimeter, acreage, containment percentage, and whether the event is a wildfire or prescribed fire.

Match a group of FIRMS detections to a WFIGS incident by checking whether the points fall inside or near an official perimeter. Not every fire has a current perimeter, so FIRMS should still work on its own when no official incident matches.

Limitations

A FIRMS point is a satellite-detected heat anomaly, not a confirmed wildfire. It could also come from a prescribed burn, volcano, gas flare, or another heat source. The point shows the center of a satellite pixel, not the exact fire location or perimeter.

You also can’t use the number of points to calculate how many acres are burning. Clouds, smoke, satellite timing, and differences between sensors can cause detections to appear or disappear. No recent detection doesn’t mean there’s no fire.

Weather forecasts cover an area rather than the exact fire location, and air-quality monitors may be several miles away. AirNow readings are also preliminary when first published. The app should be treated as an informational project, not an evacuation tool or emergency warning system.

Links

NASA FIRMS: https://firms.modaps.eosdis.nasa.gov/

FIRMS Area API: https://firms.modaps.eosdis.nasa.gov/api/area/

Free FIRMS MAP_KEY: https://firms.modaps.eosdis.nasa.gov/api/map_key/

FIRMS data fields: https://www.earthdata.nasa.gov/data/tools/firms/active-fire-data-attributes-modis-viirs

National Weather Service API: https://www.weather.gov/documentation/services-web-api

NWS forecast-grid data: https://weather-gov.github.io/api/gridpoints

EPA AirNow API: https://docs.airnowapi.org/webservices

AirNow hourly files: https://files.airnowtech.org/

US Census Geocoder: https://geocoding.geo.census.gov/geocoder/

NIFC wildfire data: https://data-nifc.opendata.arcgis.com/


