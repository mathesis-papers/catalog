#!/usr/bin/env ruby
# Fallback when actionlint is unavailable: parse YAML, validate controls and bash -n each run block.
require 'yaml'
require 'tempfile'
require 'open3'
workflow = YAML.load_file('.github/workflows/publish.yml')
triggers = workflow['on'] || workflow[true] # YAML 1.1 treats "on" as a boolean.
raise 'daily + manual triggers required' unless triggers['schedule'].size == 1 && triggers.key?('workflow_dispatch')
raise 'excess workflow permissions' unless workflow['permissions'] == {'contents' => 'read'}
raise 'overlapping runs allowed' unless workflow['concurrency']['cancel-in-progress'] == false
steps = workflow['jobs']['publish']['steps']
steps.each do |step|
  if step['uses']
    raise "action is not pinned: #{step['uses']}" unless step['uses'].match?(/@[0-9a-f]{40}$/)
  end
  next unless step['run']
  Tempfile.create(['mathesis-workflow-', '.sh'], ENV['TMPDIR']) do |file|
    file.write(step['run'])
    file.flush
    output, status = Open3.capture2e('bash', '-n', file.path)
    raise "#{step['name']}: #{output}" unless status.success?
  end
end
Dir['scripts/*.sh'].each do |script|
  output, status = Open3.capture2e('bash', '-n', script)
  raise "#{script}: #{output}" unless status.success?
end
puts 'Workflow YAML, triggers, permissions, action pins and every shell block/script passed.'
