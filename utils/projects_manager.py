
import sys, re, os, json, shutil, platform, glob
from pathlib import Path
from tkinter import messagebox


# 添加项目根目录到Python路径
project_root = Path(__file__).parent
project_root = project_root.parent
sys.path.insert(0, str(project_root))

# 导入日志系统
from utils.logger import log_info, log_error, log_warning, log_exception, log_debug, LogContext, log_function
import pandas as pd
class ProjectManager:
    def __init__(self):
        """初始化项目"""
        pass

    class DataContainer:
        def __init__(self):
            self.projects = {}
            self.project = None
            self.output_dir = None
            self.system = platform.system()
            self.projects_info = {}
            self.dir_settings = None
            self.settings = None
            self.easyqc_dir = project_root
            self.var = {}
            self.tab = {}
            self.var['ezqc_new'] = None
            self.var['ezqc_filter'] = None
            self.var['ezqc_all'] = None
            self.tab['ezqc_qctable'] = None

        def __contains__(self, key):
            """支持'in'操作符"""
            return hasattr(self, key)
        
    # @log_function("ProjectManager")
    def init_projects(self, app=None, cbProjM=None):
        """初始化项目列表"""
        self.app = app
        self.cbProjM = cbProjM
        self.dt = self.DataContainer()

        pt_json_projects = os.path.join(project_root, 'projects.json')
        
        if os.path.exists(pt_json_projects):
            with open(pt_json_projects, 'r', encoding='utf-8') as f:
                self.dt.projects_info = json.load(f)
            self.dt.projects = self.dt.projects_info['projects']
            self.dt.project = self.dt.projects_info['last_project']
            print(f" self.project, {self.dt.project}")
            if self.dt.project and self.dt.project in self.dt.projects:
                self.dt.output_dir = self.dt.projects[self.dt.project]
            else:
                self.dt.output_dir = None
        else:
            # 初始化项目列表
            projects_info = {"projects": {},"last_project": None}
            with open(pt_json_projects, 'w', encoding='utf-8') as f:
                json.dump(projects_info, f, indent=4, ensure_ascii=False)
            self.init_projects(app=self.app, cbProjM=self.cbProjM)  # 递归调用重新加载

    def save_projects_info(self):
        """保存项目信息"""
        if self.dt.project is None:
            return
        with open(os.path.join(self.dt.easyqc_dir, 'projects.json'), 'w', encoding='utf-8') as f:
            json.dump(self.dt.projects_info, f, indent=4, ensure_ascii=False)

    def create_project(self, name, path):
        """创建项目"""

        try:
            dirname = os.path.dirname(path)
            if not dirname.startswith("easyqc_"):
                path = os.path.join(path, f"easyqc_{name}")
            self.dt.dir_settings = os.path.join(path, f"settings_{name}.json")

            if dirname == f"easyqc_{name}" or os.path.exists(path):
                log_warning(f"项目 '{name}' 已存在, 跳过创建", "ProjectManager")
                messagebox.showinfo("成功", f"项目 '{name}' 已存在, 将导入项目")
            else:
                os.makedirs(path, exist_ok=True)
                messagebox.showinfo("成功", f"项目 '{name}' 创建成功")

            self.new_settings()
            self.dt.projects[name] = str(path)
            self.dt.projects_info['last_project'] = name
            self.dt.projects_info['projects'] = self.dt.projects
            self.dt.output_dir = path
            self.dt.project = name
            self.save_projects_info()
            self.load_project(project=name)

            log_info(f"项目 '{name}' 创建成功", "ProjectManager")

        except Exception as e:
            log_exception(f"创建项目 '{name}' 失败: {e}", "ProjectManager")

    def new_settings(self):
        """新建设置"""
        self.dt.settings = {
            "constants":{},
            "variables":{},
            "var_select_filter":None,
            "select_filter":None,
            "qcmodule":{}
        }
        self.dt.settings['qcmodule'] = self.add_qcmodule(self.dt.settings['qcmodule'], 1, "example", "Example")
        return self.dt.settings

    def change_project(self, project):
        self.save_settings()
        self.load_project(project)

    def load_project(self, project=None, output_dir=None, fresh_gui=True):
        """加载项目"""

        if output_dir is not None:
            for file in os.listdir(output_dir):
                if file.startswith("settings_") and file.endswith(".json"):
                    project = file[9:-5]
                    pt_json = os.path.join(output_dir, file)
                    break
            if os.path.exists(pt_json):
                self.dt.projects[project] = output_dir
                self.dt.projects_info['last_project'] = project
                self.dt.project = project
                self.dt.output_dir = output_dir
                self.save_projects_info()
                self.load_project(project)
            else:
                log_error("未找到设置文件", "ProjectManager")
        else:
            self.dt.project = project
            self.dt.projects_info['last_project'] = project
            self.dt.output_dir = self.dt.projects[project] if project in self.dt.projects else None
            self.load_settings(project)

            self.load_ratings()
            self.load_table()
            self.save_projects_info()

            # 调用回调函数刷新常量表格
            if fresh_gui:
                self.cbProjM.load_project_to_gui()


    def load_settings(self, project=None):
        """加载设置"""
        if project is None:
            self.new_settings()
        else:
            output_dir = self.dt.projects[project]
            self.dt.dir_settings = os.path.join(output_dir, f"settings_{project}.json")

            if os.path.exists(self.dt.dir_settings):
                with open(self.dt.dir_settings, 'r', encoding='utf-8') as f:
                    self.dt.settings = json.load(f)
                log_info(f"项目 '{project}' 设置加载成功", "ProjectManager")
            else:
                log_warning(f"设置文件不存在，将创建新文件 {self.dt.dir_settings}", "ProjectManager")
                self.new_settings()
                self.save_settings()

        

    def load_table(self,type=None):
        if self.dt.project is None:
            return
        table_dir = os.path.join(self.dt.output_dir, 'Table')

        if type is None:
            self.load_table('ezqc_all')
            self.load_table('ezqc_qctable')
            self.load_table('table')
        elif type == 'ezqc_all':
            ptcsv = os.path.join(table_dir, 'ezqc_all.csv')
            if os.path.exists(ptcsv):
                self.dt.var['ezqc_all'] = pd.read_csv(ptcsv, encoding='utf-8')
            else:
                self.dt.var['ezqc_all'] = None
        elif type == 'ezqc_qctable':
            pass
        elif type == 'table':
            for key in self.dt.settings['qcmodule'].keys():
                name = self.dt.settings['qcmodule'][key]['name']
                ptcsv = os.path.join(table_dir, f'ezqc_{name}.csv')
                if os.path.exists(ptcsv):
                    self.dt.tab[name] = pd.read_csv(ptcsv, encoding='utf-8')
                else:
                    self.dt.tab[name] = None
        

    def save_table(self,type=None):
        if self.dt.project is None or self.dt.output_dir is None:
            return

        table_dir = os.path.join(self.dt.output_dir, 'Table')
        os.makedirs(table_dir, exist_ok=True)
        if type is None:
            self.save_table('ezqc_all')
            self.save_table('ezqc_qctable')
            self.save_table('table')

        elif type == 'ezqc_all':
            ptcsv = os.path.join(table_dir, 'ezqc_all.csv')
            if self.dt.var['ezqc_all'] is not None:
                self.dt.var['ezqc_all'].to_csv(ptcsv, index=False, encoding='utf-8')
            else:
                log_warning(f"ezqc_all 表格为空，未保存")

        elif type == 'ezqc_qctable':
            pass
        elif type == 'table':
            for name, table in self.dt.tab.items():
                if table is not None:
                    table.to_csv(os.path.join(table_dir, f'ezqc_{name}.csv'), index=False, encoding='utf-8')

    def save_settings(self):
        """保存设置"""
        if self.dt.project is None or self.dt.output_dir is None:
            return

        dir_settings = os.path.join(self.dt.output_dir, f"settings_{self.dt.project}.json")
        with open(dir_settings, 'w', encoding='utf-8') as f:
            json.dump(self.dt.settings, f, indent=4, ensure_ascii=False)


    def rm_project(self, project):
        """移除项目"""
        if project in self.dt.projects:
            del self.dt.projects[project]
            if self.dt.project == project:
                if self.dt.projects:
                    self.dt.project = list(self.dt.projects.keys())[0]
                    self.dt.output_dir = self.dt.projects[self.dt.project]
                else:
                    self.dt.project = None
                    self.dt.output_dir = None
            self.dt.projects_info['last_project'] = self.dt.project
            self.dt.projects_info['projects'] = self.dt.projects
            self.save_projects_info()
            self.load_project(self.dt.project)
            
        else:
            log_error(f"项目 '{project}' 不存在", "ProjectManager")

    def modify_qcmodule(self, index, name_, label_, index_):
        """修改qcmodule"""
        module = self.dt.settings['qcmodule'][index].copy()
        module['name'] = name_
        module['label'] = label_
        del self.dt.settings['qcmodule'][index]
        
        # 获取所有现有的index并排序
        existing_indices = sorted([int(k) for k in self.dt.settings['qcmodule'].keys()])
        new_index = int(index_)
        
        # 创建临时字典存储重新排序后的模块
        temp_modules = {}
        
        # 找到新index应该插入的位置（基于排序后的位置，而不是数值）
        insert_position = 0
        for i, idx in enumerate(existing_indices):
            if new_index <= idx:
                insert_position = i
                break
            insert_position = i + 1
        
        # 重新分配所有模块的index
        final_index = 1
        for i in range(len(existing_indices) + 1):
            if i == insert_position:
                # 在指定位置插入新模块
                temp_modules[str(final_index)] = module
                final_index += 1
            
            # 添加原有模块（如果还有的话）
            if i < len(existing_indices):
                old_idx = existing_indices[i]
                old_idx_str = str(old_idx)
                temp_modules[str(final_index)] = self.dt.settings['qcmodule'][old_idx_str]
                final_index += 1
        
        # 更新qcmodule字典
        self.dt.settings['qcmodule'] = temp_modules

    def add_qcmodule(self, dict, index, name, label):
        """添加qcmodule"""
        if hasattr(self.dt.settings, 'qcmodule') and name in self.dt.settings.qcmodule:
            log_error(f"模块 '{name}' 已存在", "ProjectManager")
            return

        # 确保数值参数为整数类型
        try:
            index = int(index)
        except (ValueError, TypeError) as e:
            log_error(f"参数类型转换失败: {str(e)}", "ProjectManager")
            return
        
        module = {
            'name': name,
            'label': label,
            'ezqcid': None,
            'rater': None,
            'watch_mode': False,
            'interper': 'shell',
            'code': None,
            'code_exe': None,
            'tags': {'1': {'label': None, 'value': None}},
            'scores': {'1': {'label': None, 'num': None, 'num_': None, 'value': None}},
            'notes': None,
            'time': None,
            'control': False,
            'showing': True,
            'select_filter': None,
            'button': {}
        }

        return self.add_key(dict, index, module)



    def add_key(self, dict, index, module=None):
        """添加或删除字典中的子字典
        
        Args:
            dict: 目标字典，key为正整数字符串
            index: 要操作的序号（正整数）
            module: 要添加的子字典内容，如果为None则删除对应的index
        
        功能说明：
        - 如果module不为None：添加子字典到指定index位置
          - 如果index已存在，则插队（原index及之后的key都+1）
          - 如果index不存在，则直接添加
        - 如果module为None：删除指定index的子字典，并重新排序剩余的key
        """
        try:
            # 删除操作
            if module is None:
                if str(index) in dict:
                    del dict[str(index)]
                    # 重新排序剩余的键，确保连续性
                    keys = sorted([int(k) for k in dict.keys()])
                    new_dict = {}
                    for i, old_key in enumerate(keys, 1):
                        new_dict[str(i)] = dict[str(old_key)]
                    dict.clear()
                    dict.update(new_dict)
                    log_info(f"删除index {index}并重新排序完成", "ProjectManager")
                else:
                    log_warning(f"要删除的index {index}不存在", "ProjectManager")
                return dict
            
            # 添加操作
            # 获取当前字典中的所有键并转换为整数
            keys = [int(k) for k in dict.keys()] if dict else []
            
            # 如果字典为空，直接添加第一个元素
            if not keys:
                dict["1"] = module
                log_info(f"添加第一个元素到index 1", "ProjectManager")
                return dict
            
            # 如果指定的index已存在，需要插队
            if str(index) in dict:
                # 获取最大键值
                max_key = max(keys)
                # 从最大键开始向后移动，为新元素腾出位置
                for i in range(max_key, index-1, -1):
                    if str(i) in dict:
                        dict[str(i+1)] = dict[str(i)]
                log_info(f"插队操作：index {index}及之后的元素后移", "ProjectManager")
            
            # 插入新值
            dict[str(index)] = module
            log_info(f"成功添加module到index {index}", "ProjectManager")

            return dict
            
        except Exception as e:
            log_error(f"add_key操作失败: {str(e)}", "ProjectManager")
            raise
        
    def load_rating_json(self, filename):
        """从json文件中读取数据并转换为pandas DataFrame"""
        try:
            # 保存原始文件路径用于读取文件
            original_filename = filename
            # 从文件名中提取信息
            basename = os.path.basename(filename)
            parts = basename.replace('.json', '').split('._.')
            ezqcid = parts[1]

            with open(original_filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if isinstance(data, dict) and data['ezqcid'] == ezqcid:
                # 创建展平后的数据字典
                flattened_data = {}
                
                # 处理顶级字段
                for key, value in data.items():
                    if key not in ['scores', 'tags', 'code_exe']:
                        flattened_data[key] = value
                # 处理 code_exe 字段
                if 'code_exe' in data and isinstance(data['code_exe'], dict):
                    for code_key, code_value in data['code_exe'].items():
                        if isinstance(code_value, dict):
                            flattened_data[f"code_exe"] = ''
                            for sub_key, sub_value in code_value.items():
                                flattened_data[f"code_exe"] = f"{flattened_data[f'code_exe']}; {sub_value};"
                        else:
                            flattened_data[f"code_exe"] = code_value
                
                # 展平 scores 字段
                if 'scores' in data and isinstance(data['scores'], dict):
                    for score_key, score_value in data['scores'].items():
                        if isinstance(score_value, dict):
                            for sub_key, sub_value in score_value.items():
                                sub_key = '' if sub_key == 'value' else sub_key
                                flattened_key = f"score{score_key}{sub_key}"
                                flattened_data[flattened_key] = sub_value
                        else:
                            flattened_data[f"score{score_key}"] = score_value
                
                # 展平 tags 字段
                if 'tags' in data and isinstance(data['tags'], dict):
                    for tag_key, tag_value in data['tags'].items():
                        if isinstance(tag_value, dict):
                            for sub_key, sub_value in tag_value.items():
                                sub_key = '' if sub_key == 'value' else sub_key
                                flattened_key = f"tag{tag_key}{sub_key}"
                                flattened_data[flattened_key] = sub_value
                        else:
                            flattened_data[f"tag{tag_key}"] = tag_value
                
                # 创建单行DataFrame，使用ezqcid作为索引
                flattened_data['filename'] = basename
                flattened_data['filepath'] = original_filename

                df = pd.DataFrame([flattened_data])
                # 将name列转换为module_name
                df['module_name'] = df['name']
                df = df.drop(columns=['name'])
                
            else:
                df = pd.DataFrame({'ezqcid': ezqcid, 'filename': basename, 'filepath': original_filename})
                
            return df
            
        except Exception as e:
            log_error(f"读取JSON文件 {original_filename} 时出错: {str(e)}")
            return pd.DataFrame()

    def load_ratings(self):
        """
        遍历 ratingFiles 目录下的所有 JSON 文件，读取内容并合并成 pandas DataFrame
        """
        log_info(f"开始读取评分文件")
        if self.dt.project is None:
            return
        dir_ratingFiles = os.path.join(self.dt.output_dir, 'ratingFiles')
        
        # 检查目录是否存在
        if not os.path.exists(dir_ratingFiles):
            log_warning(f"评分文件目录不存在: {dir_ratingFiles}")
            return pd.DataFrame()
        
        # 遍历 module 目录
        data_rows = []
        
        module_dirs = [d for d in os.listdir(dir_ratingFiles) 
                      if os.path.isdir(os.path.join(dir_ratingFiles, d))]
        
        if not module_dirs:
            log_info(f"在目录 {dir_ratingFiles} 中未找到 module 子目录")
            return pd.DataFrame()
        
        for module_name in module_dirs:
            module_path = os.path.join(dir_ratingFiles, module_name)
            
            # 遍历 rater 目录
            rater_dirs = [d for d in os.listdir(module_path) 
                         if os.path.isdir(os.path.join(module_path, d))]
            
            for rater_name in rater_dirs:
                rater_path = os.path.join(module_path, rater_name)
                
                # 获取该 rater 目录下的所有 JSON 文件
                json_files = glob.glob(os.path.join(rater_path, '*.json'))
                
                for json_file in json_files:
                    try:
                        # 解析文件名验证 module 和 rater
                        filename = os.path.basename(json_file)
                        
                        # 文件名格式: module._.ezqcid._.rater._.score1._.tag1.json
                        parts = filename.replace('.json', '').split('._.')
                        
                        if len(parts) >= 3:
                            file_module = parts[0]
                            file_rater = parts[2]
                            
                            # 验证 module 和 rater 是否与目录结构一致
                            if file_module == module_name and file_rater == rater_name:
                                 # 将数据添加到列表中
                                data_rows.append(self.load_rating_json(json_file))
                                
                                log_debug(f"成功读取文件: {json_file}")
                            else:
                                log_warning(f"文件 {json_file} 的 module({file_module}) 或 rater({file_rater}) 与目录结构不一致，跳过")
                        else:
                            log_warning(f"文件名格式不正确，跳过: {json_file}")
                            
                    except Exception as e:
                        log_error(f"读取文件 {json_file} 时出错: {str(e)}")
                        continue
        
        # 将所有数据合并成 DataFrame
        if data_rows:
            # 过滤掉空的DataFrame
            valid_dfs = [df for df in data_rows if not df.empty]
            if valid_dfs:
                df_ezqc_qctable_orig = pd.concat(valid_dfs, ignore_index=False).sort_index()
                self.gen_rating_dict(df_ezqc_qctable_orig)
                table_dir = os.path.join(self.dt.output_dir, 'Table','ezqc_qctable_orig.csv')
                df_ezqc_qctable_orig.to_csv(table_dir, index=False)
                # 进行透视表转换,保留ezqcid列不变
                df_ezqc_qctable_orig_wide = df_ezqc_qctable_orig.pivot_table(
                    index='ezqcid',
                    columns=['module_name', 'rater'],
                    aggfunc='first'
                ).reset_index()

                new_columns = []
                for col in df_ezqc_qctable_orig_wide.columns:
                    if col == 'ezqcid' or (isinstance(col, tuple) and col[0] == 'ezqcid'):  # 单级列或多级列中的ezqcid
                        new_columns.append('ezqcid')
                    else:  # 多级列
                        new_columns.append(f"{col[1]}.{col[2]}.{col[0]}")

                df_ezqc_qctable_orig_wide.columns = new_columns
                df_ezqc_qctable_orig_wide['ezqcid'] = df_ezqc_qctable_orig_wide['ezqcid'].astype(str)
                table_dir = os.path.join(self.dt.output_dir, 'Table','ezqc_qctable_orig_wide.csv')
                df_ezqc_qctable_orig_wide.to_csv(table_dir, index=False)

                # 如果ezqc_all存在，则合并df_wide到ezqc_all中
                if self.dt.var['ezqc_all'] is not None:
                    # 基于ezqcid进行内连接,只保留df_ezqc_qctable_orig_wide中存在的ezqcid行
                    df_ezqc_qctable_orig_wide = pd.merge(
                        self.dt.var['ezqc_all'],
                        df_ezqc_qctable_orig_wide,
                        on='ezqcid',
                        how='inner'
                    )

                # 删除df_ezqc_qctable_orig_wide中包含.code和.code_exe的列，将ezqcid放在第一列，让包含.score【1-n】的列，以及.tag【1-n】的列在ezqcid后面
                df_ezqc_qctable_orig_wide = df_ezqc_qctable_orig_wide.drop(columns=[col for col in df_ezqc_qctable_orig_wide.columns if '.code' in col or '.code_exe' in col])
                
                score_cols = [col for col in df_ezqc_qctable_orig_wide.columns if re.search(r'\.score\d+$', col) and col != 'ezqcid']
                tag_cols = [col for col in df_ezqc_qctable_orig_wide.columns if re.search(r'\.tag\d+$', col) and col != 'ezqcid']
                other_cols = [col for col in df_ezqc_qctable_orig_wide.columns if col != 'ezqcid' and not re.search(r'\.score\d+$', col) and not re.search(r'\.tag\d+$', col)]
                
                new_column_order = ['ezqcid'] + score_cols + tag_cols + other_cols
                df_ezqc_qctable_orig_wide = df_ezqc_qctable_orig_wide[new_column_order]

                self.dt.tab['ezqc_qctable'] = df_ezqc_qctable_orig_wide
                self.save_table('ezqc_qctable')
            else:
                log_warning("没有成功读取任何评分文件")
                self.dt.tab['ezqc_qctable'] = pd.DataFrame()
        else:
            log_warning("没有成功读取任何评分文件")
            self.dt.tab['ezqc_qctable_'] = pd.DataFrame()


    def gen_rating_dict(self, df):
        """
        生成评分字典，支持多评分、多标签，自动适配DataFrame的行类型
        """
        self.rating_dict = {}
        if df is None or len(df) == 0:
            return

        # 检查DataFrame的列
        if 'ezqcid' not in df.columns:
            log_warning("DataFrame中没有ezqcid列，无法生成评分字典")
            return

        # 兼容DataFrame的iterrows()返回(row_index, row_series)
        ezqcids = list(set(df['ezqcid'].tolist()))
        for ezqcid in ezqcids:
            df_ezqcid = df[df['ezqcid'] == ezqcid]
            self.rating_dict[ezqcid] = {}
            for _, row in df_ezqcid.iterrows():
                try:
                    # 从文件名中提取module_name和rater信息
                    filename = row.get('filename', '')
                    if filename:
                        parts = filename.replace('.json', '').split('._.')
                        if len(parts) >= 3:
                            module_name = parts[0]
                            rater = parts[2]
                        else:
                            module_name = 'unknown'
                            rater = 'unknown'
                    else:
                        module_name = 'unknown'
                        rater = 'unknown'

                    # 收集所有score和tag列
                    score_dict = {}
                    tag_dict = {}
                    
                    for col in df.columns:
                        if col.startswith('score') and col != 'ezqcid':
                            value = row.get(col)
                            if value is not None and value != '':
                                score_dict[col] = value
                        elif col.startswith('tag') and col != 'ezqcid':
                            value = row.get(col)
                            if value is not None and value != '':
                                tag_dict[col] = value

                    # 合并所有信息
                    entry = {
                        'module_name': module_name,
                        'rater': rater,
                    }
                    entry.update(score_dict)
                    entry.update(tag_dict)
                    self.rating_dict[ezqcid][f"{module_name}-{rater}"] = entry
                except Exception as e:
                    log_warning(f"生成评分字典时出错: {e}，行内容: {row}")

        




             
    



            

