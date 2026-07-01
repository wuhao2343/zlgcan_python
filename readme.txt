2025/12/11：
	
	新增 USBCANFD系列，CANFDNET系列 的总线利用率上报功能 演示
	注意：在选择对应例程之后，opendevice函数一定要选择对应型号的设备


2025/08/25：
	
	
	所有二次开发的基础，建议先用软件ZCANPRO/ZXDOC 调通后再进行开发
	ZCANPRO 下载链接：https://manual.zlg.cn/server/index.php?s=/api/attachment/visitFile/sign/f6fc240df12d1ed7cf2ef089df0e94c5
	ZXDOC 	下载链接：https://manual.zlg.cn/server/index.php?s=/api/attachment/visitFile/sign/22ef34679ba463add61ca42d09839377
	
	
dll位数不匹配:
	
	例程默认调用的是64位的库，可以将函数库中的x86文件夹中的文件（kerneldlls文件夹+zlgcan.dll），替换即可
	
USBCANFD系列：

	USBCANFD系列在Win10即以上系统免驱。

	USBCANFD系列老卡：USBCANFD-100U 设备侧面白色标签纸右上角 V1.01及以下（包括V1.01）
					  USBCANFD-200U V1.02及以下（包含V1.02）
	其他版本都数据新卡
			
		老卡二次开发需注意：
				1.队列发送：将每帧报文排成长队，严格按照报文设置时间间隔（单位：1ms/0.1ms）发送报文。发送方式为单次发送，不考虑发送失败的情况
						应用场景：需要周期发送，且需要频繁修改报文内容的情况（弥补定时发送不能频繁修改报文的情况，处理机制不一样）
								  想实现周期发送，需要自行计算每帧报文发送间隔。=
						
						老卡队列发送：需要通过ZCAN_SetValue设置使能队列发送的同时，报文结构体的_pad/flags的bit7置1使能队列发送
									  新卡只需要 执行后者，与setvalue无关
									  
				2.定时发送：设置报文按固定周期发送报文。发送方式同样为单次发送，不考虑发送失败的情况
						通过定时发送结构体，和ZCAN_SetValue设置定时发送序号以及周期，报文内容通过序号索引修改
						
						老卡定时发送：与队列发送不能同时使用，使用定时发送时，需要ZCAN_SetValue关闭队列发送
									  新卡支持同时使用
  
	ZCAN_SetValue 的字符串参数需要严格匹配，包括字符串大小写，当功能不生效时，请有限检查参数是否书写正确
	
	can_frame / canfd_frame 结构体的 _pad / flags ：标识位的作用
	
						bit0:   brs设置加速报文0x1；	_pad设置无效（CAN报文没有加速的说法）
						bit5：设置回显0x20；
						bit6:   设置队列发送时间精度，置1使能0.1ms精度0x40；置0精度为毫秒
						bit7:   设置队列发送0x80；
						
	扩展帧设置	ID 最高位（bit31）为扩展帧标识	置一为扩展帧 
				同理 bit30 为远程帧标识			置一为远程帧
				
CANFDNET系列：	

	设备的使用可以先看在线文档：https://manual.zlg.cn/web/#/131/8292

	CANFDNET是网络通讯产品，本质是PC与CANFDNET设备建立网络连接，进行网络包通讯，所有以太网产品不需要驱动。

	那到设备第一步是给设备进行配置：
			（CANFDNET默认IP是192.168.0.178 工作模式是TCP Server 工作端口为8000）
			1，配置CANFDNET的IP，保证与电脑网口同一网段
			2，配置波特率，与产品保持一致
			3，配置工作模式，TCP服务器/TCP客户端/UDP 工作端口，目标端口 这些
				当CANFDNET 作为TCP服务器，那么PC就当客户端
						   作为TCP客户端，那么PC就当服务器
		检测与CANFDNET是否连接上，可以通过读取设备信息来判断。
			当CANFDNET作为客户端时，PC作为服务器，
			PC作为服务器的IP为网口连接的IP，配置时需要确定好这个IP
			启动服务器（startCAN）后 需要等待客户端连接，
			可以进行延时3秒等待连接或getDevicenet判断连接情况
			
CANET系列：

	这个系列的设备，每个通道都需要建立一个连接，不论设备作为TCP服务器/客户端/UDP
	
PCIECANFD系列：

	PCIECANFD-200U （老卡）,可通过固件升级，升级成新卡，老卡二次开发 device_Type = 39,老卡存在定时发送异常的问题
	PCIECANFD-200U-EX 新卡，解决老卡的问题， device_Type = 62/63
	
		如何区分新老卡:通过驱动信息，找到硬件ID 7011=老卡，9A02=新卡 	---具体操作流程可以百度“如何查看驱动的硬件ID”
		
CANDTU系列：

	在start_can之后，建议加入延时，以保证设备能正常操作通道
	
	
自定义波特率:

    ZCAN_SetValue(device_handle, str(chn) + "/baud_rate_custom", "500Kbps(80%),2.0Mbps(80%),(80,07C00002,01C00002)"）

	需要ZCANPRO/ZXDOC 工具中的【波特率计算器】,计算出结果，作为字符串拷贝到set_value的最后一个参数
	当产品采用不常规的波特率，例如666Kbps	---常规设置无法满足此需求，则需要自定义波特率设置
	或者当产品波特率采用不常见采样点，例如500kbps 75%	---- 500K波特率 75%的采样点，常规默认是80%的采样点
	