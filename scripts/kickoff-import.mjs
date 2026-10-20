// Read the actual 开项 export through its pinned, unmodified model.
import {readFileSync} from 'node:fs';
import {normalize, validate, handoff, brief} from '../vendor/kickoff/model.mjs';
try {
  const raw = JSON.parse(readFileSync(process.argv[2], 'utf8'));
  if(raw.app !== 'project-kickoff' || raw.version !== 1 || !raw.project?.data)
    throw Error('需要开项「备份 JSON」导出的 version 1 项目文件');
  if(raw.project.mode === 'idea') throw Error('想法尚未转为项目，先在开项里转为项目草稿');
  const issues = [], data = normalize(raw.project.data, issues), h = handoff(data);
  console.log(JSON.stringify({name:data.name, data, brief:brief(data), handoff:h,
    missing:validate(data), issues, source_project_id:raw.project.id || null,
    import_only:true, development_authorized:false}));
} catch(e) { console.error(e.message); process.exitCode=2; }
