<script>
	import '@evidence-dev/tailwind/fonts.css';
	import '../app.css';
	import { EvidenceDefaultLayout } from '@evidence-dev/core-components';
	export let data;
</script>

<EvidenceDefaultLayout
	{data}
	title="Sydney timetable honesty"
	githubRepo="https://github.com/tuannm3812/sydney-transit-lakehouse"
	homePageName="Overview"
>
	<slot slot="content" />
</EvidenceDefaultLayout>
