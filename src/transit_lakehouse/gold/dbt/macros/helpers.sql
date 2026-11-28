{# Seconds since the GTFS service-day origin -> UTC timestamp.
   GTFS times count from noon minus 12 h local time, which is local
   midnight except on daylight-saving change days. #}
{% macro gtfs_to_utc(origin, seconds) -%}
    ({{ origin }} + to_seconds(({{ seconds }})::bigint))
{%- endmacro %}

{# The UTC instant a service day's GTFS clock starts. #}
{% macro service_day_origin(service_date) -%}
    ((({{ service_date }})::timestamp + interval 12 hour)
        at time zone '{{ var("sydney_tz") }}') - interval 12 hour
{%- endmacro %}

{% macro gtfs_date(text) -%}
    try_strptime({{ text }}, '%Y%m%d')::date
{%- endmacro %}

{# Fallback mode for a route type the network seed does not cover
   (GTFS and extended route types). #}
{% macro mode_of_route_type(rt) -%}
    case
        when {{ rt }} = 401 or {{ rt }} = 1 then 'metro'
        when {{ rt }} = 2 or {{ rt }} between 100 and 117 then 'train'
        when {{ rt }} = 0 or {{ rt }} between 900 and 906 then 'lightrail'
        when {{ rt }} = 4 or {{ rt }} between 1000 and 1200 then 'ferry'
        when {{ rt }} = 3 or {{ rt }} between 700 and 716 then 'bus'
        when {{ rt }} between 200 and 209 then 'coach'
        else 'other'
    end
{%- endmacro %}

{% macro in_service_window(col) -%}
    {%- set lo = var("service_date_from") -%}
    {%- set hi = var("service_date_to") -%}
    {%- if lo %} and {{ col }} >= date '{{ lo }}'{% endif -%}
    {%- if hi %} and {{ col }} <= date '{{ hi }}'{% endif -%}
{%- endmacro %}

{# Great-circle distance in metres. #}
{% macro haversine_m(lat1, lon1, lat2, lon2) -%}
    (2 * 6371000 * asin(sqrt(
        pow(sin(radians({{ lat2 }} - {{ lat1 }}) / 2), 2)
        + cos(radians({{ lat1 }})) * cos(radians({{ lat2 }}))
        * pow(sin(radians({{ lon2 }} - {{ lon1 }}) / 2), 2)
    )))
{%- endmacro %}

{# A SQL list of the modes in a list variable, e.g. ('train'). #}
{% macro mode_list(name) -%}
    ('{{ var(name) | join("', '") }}')
{%- endmacro %}

{# A zero delay from a feed that never reports early running (D-059). #}
{% macro early_hidden() -%}
    (mode in {{ mode_list('no_early_modes') }}
        and predicted_arrival_utc <= left_feed_at and row_delay_s = 0)
{%- endmacro %}
