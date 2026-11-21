{# Use the configured schema name as is (gold, staging), not
   "<target>_<schema>": the database file is already per target. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name else target.schema }}
{%- endmacro %}
