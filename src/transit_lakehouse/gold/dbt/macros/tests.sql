{# A share column must be at least `threshold` on every row. #}
{% test share_at_least(model, column_name, threshold) %}
select *
from {{ model }}
where {{ column_name }} is not null
  and {{ column_name }} < {{ threshold }}
{% endtest %}

{# The listed columns identify a row. #}
{% test unique_combination(model, columns) %}
select {{ columns | join(", ") }}, count(*) as n
from {{ model }}
group by all
having count(*) > 1
{% endtest %}
