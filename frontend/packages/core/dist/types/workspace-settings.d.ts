export interface AgentConfig {
    system_prompt: string;
}
export interface SkillInstall {
    install_id: string;
    skill_id: string;
    name: string;
    description: string;
    installed_version: string;
    enabled: boolean;
    scope: 'org' | 'workspace';
}
export interface WorkspaceSkills {
    org_skills: SkillInstall[];
    workspace_skills: SkillInstall[];
}
//# sourceMappingURL=workspace-settings.d.ts.map