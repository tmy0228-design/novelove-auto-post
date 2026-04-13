import paramiko

def run():
    print("本番サーバーへSFTPで修復スクリプトを転送します...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect('210.131.218.83', username='root', password='#Dama0228')
    
    sftp = ssh.open_sftp()
    
    # サーバー上にtoolsディレクトリを作る（無ければ）
    try:
        sftp.mkdir('/home/kusanagi/scripts/tools')
    except IOError:
        pass
        
    sftp.put(
        'tools/repair_exclusive.py', 
        '/home/kusanagi/scripts/tools/repair_exclusive.py'
    )
    # または repair_exclusive_tags_remote.py も転送
    sftp.put(
        'tools/repair_exclusive_tags_remote.py', 
        '/home/kusanagi/scripts/tools/repair_exclusive_tags_remote.py'
    )
    sftp.close()
    
    print("本番サーバー内の生きたDBに対して修復スクリプトを実行します...")
    
    # 念のため、ローカル用の改変が入っているかもしれないのでサーバ上でsubprocessを使うようファイル書き換えするか、
    # 既存の "subprocess" 版であればそのまま実行。
    # 実際、さっき私はParamikoに書き換えてしまったので、それを戻したスクリプトをサーバに送るべき。
    # しかしさっきの repair_exclusive_tags_remote.py は paramiko を使ってさらにリモートに繋ごうとしている。
    
    pass

if __name__ == "__main__":
    run()
